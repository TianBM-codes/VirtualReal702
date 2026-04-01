from flask import request
import json

def log_request():
    """最简版本"""
    if request.method != "POST":
        return  # 如果不是 POST 请求，直接返回

    try:
        request_data = request.get_json()  # 自动解析 JSON
    except Exception as e:
        request_data = {"error": "Invalid JSON", "raw_data": request.data.decode('utf-8')}

    # 结构化日志输出
    log_info = {
        "method": request.method,
        "path": request.path,
        "body": request_data,
        "client_ip": request.remote_addr,  # 可选：记录客户端 IP
    }

    # 美化输出（indent=2 让 JSON 更易读）
    print("\n[Request Log]")
    print(json.dumps(log_info, indent=2, ensure_ascii=False))
    # print(f"\n[{request.method}] {request.path} - Body: {request.data.decode('utf-8')}")

