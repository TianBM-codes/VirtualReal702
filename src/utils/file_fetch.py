"""
src/utils/file_fetch.py — 内网 HTTP 文件下载工具

用于在 ODB service runner 和 _imports service 中统一处理 "传过来的是 URL 还是本地路径" 这个问题。

典型用法：
    from src.utils.file_fetch import download_if_url

    # dest_dir 省略时自动用 service_config.json 里的 APP_DATA_ROOT
    local_path = download_if_url(url_or_path)

    # 也可以指定目录（runner 通常传 workspace 目录）
    local_path = download_if_url(url_or_path, dest_dir="/data/workspace/abc123")

纯 stdlib 实现（urllib），不依赖 httpx / requests，确保在所有 Python 3 环境均可用。
"""
import logging
import os
import shutil
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def is_http_url(s: str) -> bool:
    """判断字符串是否为 http(s):// URL。"""
    return s.startswith("http://") or s.startswith("https://")


def _apply_host_override(url: str) -> str:
    """若 APP_DOWNLOAD_HOST_OVERRIDE 已配置，替换 http:// URL 的 host:port 部分。"""
    try:
        from src.l3.core.config import settings
        override = settings.download_host_override.strip()
    except Exception:
        return url
    if not override or not url.startswith("http://"):
        return url
    parsed = urllib.parse.urlparse(url)
    rewritten = parsed._replace(netloc=override)
    new_url = urllib.parse.urlunparse(rewritten)
    if new_url != url:
        logger.info("file_fetch: host override %s → %s", parsed.netloc, override)
    return new_url


# RFC 3986 path 段里合法的 ASCII 字符（pchar + '/'），再加上 '%' 防止二次编码。
# quote 会把这些之外的字符（非 ASCII 中文、空格、控制字符等）做百分号编码，
# 而保留这些字符不动。特别注意 '[' ']'：很多文件服务器按字面名匹配带方括号的
# 文件名、并不会把 %5B/%5D 解码回去，所以必须保留字面量，否则首字符是 '[' 的
# 文件名会下载失败（404）。
_URL_PATH_SAFE = "/%" + ":@-._~!$&'()*+,;=" + "[]"


def _encode_url_path(url: str) -> str:
    """对 URL 的 path 段做百分号编码，兜底「发送端传了未编码中文/空格」的情况。

    urllib.request 发请求时按 ASCII 编码请求行，path 里有原始中文会直接抛
    UnicodeEncodeError。这里对 path 做 quote，但只编码真正不安全的字符
    （非 ASCII、空格、控制字符）；RFC 3986 path 允许的 ASCII 标点（含 '[' ']'）
    一律保留字面量。'%' 在 safe 集合里，避免对已编码串二次编码（double-encode）。
    query/host 不动。
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return url
    if not parsed.path:
        return url
    new_path = urllib.parse.quote(parsed.path, safe=_URL_PATH_SAFE)
    if new_path == parsed.path:
        return url
    return urllib.parse.urlunparse(parsed._replace(path=new_path))


def _default_dest_dir() -> str:
    """读取 service_config.json 中的 APP_DATA_ROOT 作为默认下载目录。
    懒加载，避免在模块导入时就触发 settings 初始化。"""
    try:
        from src.l3.core.config import settings
        return settings.data_root
    except Exception:
        return os.getcwd()


def download_if_url(
    url_or_path: str,
    dest_dir: str = None,
    dest_name: str = None,
) -> str:
    """如果 url_or_path 是 HTTP URL，把文件下载到 dest_dir 并返回本地路径；
    否则直接返回原路径，不做任何操作。

    参数：
        url_or_path: 本地文件路径，或 http(s):// URL
        dest_dir:    下载目标目录；省略时使用 APP_DATA_ROOT（service_config.json）
        dest_name:   下载后的文件名；省略时取 URL path 的最后一段

    返回：
        本地文件的绝对路径（字符串）

    异常：
        RuntimeError — 下载失败（连接拒绝、404、磁盘写入错误等）
    """
    if not is_http_url(url_or_path):
        return url_or_path

    url_or_path = _apply_host_override(url_or_path)
    url_or_path = _encode_url_path(url_or_path)

    if dest_dir is None:
        dest_dir = _default_dest_dir()

    if dest_name is None:
        parsed = urllib.parse.urlparse(url_or_path)
        # 去掉 query string 后取文件名；URL 没有路径时用 "download"
        raw_name = os.path.basename(parsed.path.split("?")[0]) or "download"
        # URL 里的文件名是百分号编码（中文 → %E6...），落盘前还原成可读的中文
        dest_name = urllib.parse.unquote(raw_name)

    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, dest_name)
    logger.info("file_fetch: downloading %s → %s", url_or_path, dest_path)

    try:
        urllib.request.urlretrieve(url_or_path, dest_path)
    except Exception as exc:
        raise RuntimeError(
            "Failed to download '{}': {}".format(url_or_path, exc)
        ) from exc

    size = os.path.getsize(dest_path)
    logger.info("file_fetch: done, %d bytes saved to %s", size, dest_path)
    return dest_path


def materialize_source_file(
    url_or_path: str,
    dest_dir: str = None,
    dest_name: str = None,
) -> str:
    """Make sure the source file exists as a local file under dest_dir.

    - HTTP URL: download into dest_dir and return the downloaded file path.
    - Local path: copy into dest_dir and return the copied file path.
    - If dest_dir is omitted for a local path, just return the absolute path.
    """
    if is_http_url(url_or_path):
        return download_if_url(url_or_path, dest_dir=dest_dir, dest_name=dest_name)

    source_abs = os.path.abspath(url_or_path)
    if dest_dir is None:
        return source_abs

    if dest_name is None:
        dest_name = os.path.basename(source_abs)

    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.abspath(os.path.join(dest_dir, dest_name))
    try:
        if os.path.exists(dest_path) and os.path.samefile(source_abs, dest_path):
            return dest_path
    except Exception:
        pass

    logger.info("file_fetch: copying %s -> %s", source_abs, dest_path)
    shutil.copy2(source_abs, dest_path)
    return dest_path
