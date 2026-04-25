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
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def is_http_url(s: str) -> bool:
    """判断字符串是否为 http(s):// URL。"""
    return s.startswith("http://") or s.startswith("https://")


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

    if dest_dir is None:
        dest_dir = _default_dest_dir()

    if dest_name is None:
        parsed = urllib.parse.urlparse(url_or_path)
        # 去掉 query string 后取文件名；URL 没有路径时用 "download"
        dest_name = os.path.basename(parsed.path.split("?")[0]) or "download"

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
