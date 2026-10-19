"""Build OpenAPI/Postman/Markdown from the two supplied vendor documents.

Run: python -m tools.simright.generate_artifacts
Only this generator needs openpyxl. Runtime client does not read the documents.
"""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MD = ROOT / "packages/apidoc-simright/DATA-API.md"
DEFAULT_XLSX = ROOT / "packages/接口功能说明-仿真数据获取.xlsx"
DEFAULT_OUT = ROOT / "docs/simright"
MD_PATH = "/applications/3dlite/api/v1/query"
EXCEL_PATH = MD_PATH + "/query"

EXAMPLE_ARGS = {
    "extremeValue": {"rtype": "max", "varName": "S.EL.VonMises", "stateIds": [-1]},
    "measureValue": {"measureType": 1, "entitiesIds": [{"partId": 0, "nodeId": 1},
                                                             {"partId": 0, "nodeId": 2}], "stateId": 1},
    "contourImage": {"varName": "S.EL.VonMises"},
    "modelImage": {"stateId": 1},
    "animationGif": {"varName": "S.EL.VonMises"},
    "XYCurveData1": {"nodeIds": [1], "yVarName": "D.N.X", "xVarName": "var_time", "stateIds": [1]},
    "freqValue": {"method": "order", "range": [1, 3], "modalIndex": 0},
    "freqGif": {"order": 1},
    "loadcases": {}, "variables": {},
    "curveInfo": {"dataPath": "/", "level": -1},
    "curveData": {"dataPath": ["/loadstep/glstat/time"]},
    "hitEntities": {"stateId": 1, "box": [0, 0, 0, 10, 10, 10]},
    "nodeId": {"nodePosList": [[1, 2, 3]], "stateId": 1},
    "probeGroupByPos": {"nodePosList": [[1, 2, 3]], "namePrefix": "sensor", "stateId": 1},
    "nodeInfo": {"nodeIds": [1], "varName": "S.EL.11", "stateIds": [1]},
    "elementInfo": {"elemIds": [1], "varName": "S.EL.11", "stateIds": [1]},
    "assemble": {},
    "modelDiff": {"rdb1": {"rdbPath": "p/test/model1.odb"},
                  "rdb2": {"rdbPath": "p/test/model2.odb"}},
    "deleteModelFile": {},
    "geometryParams": {"type": "face", "colors": [255, 0, 0]},
}


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def table_fields(section):
    rows = []
    for line in section.splitlines():
        cells = [x.strip().strip("`") for x in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(cells) < 3:
            continue
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9]*", cells[0]):
            continue
        rows.append({"name": cells[0], "type": cells[1], "description": cells[2],
                     "required": len(cells) > 3 and cells[3] == "是",
                     "exampleText": " | ".join(cells[4:]),
                     "sourceRow": line})
    return rows


def parse_sources(md_path, xlsx_path):
    import openpyxl

    text = md_path.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^## ", text)
    common = table_fields(next(s for s in sections if s.startswith("视图公共参数")))
    result = table_fields(next(s for s in sections if s.startswith("云图结果选项参数")))
    operations = []
    for section in sections:
        title = section.splitlines()[0] if section else ""
        match = re.search(r'name="([^"]*)"', title)
        if not match or "~~" in title:
            continue
        name = match[1] or "geometryParams"
        # curveInfo/curveData's response section is titled 响应, not 请求参数.
        request = re.split(r"(?m)^\s*### 响应", section)[0]
        fields = {}
        if re.search(r"支持\s*\[视图公共参数\]", request) or name == "modelImage":
            fields.update({r["name"]: r for r in common})
        if "[云图结果选项参数" in request:
            fields.update({r["name"]: r for r in result})
        fields.update({r["name"]: r for r in table_fields(request)})
        if name != "modelDiff":
            fields.setdefault("filename", {"name": "filename", "type": "String",
                                          "description": "RDB 相对路径", "required": True})
        # loadcase fields are described in examples/shared semantics even where omitted in tables.
        if name in ("XYCurveData1", "extremeValue"):
            for row in common:
                if row["name"] in ("loadcaseName", "loadcaseIndex"):
                    fields.setdefault(row["name"], row)
        example = copy.deepcopy(EXAMPLE_ARGS[name])
        if name != "modelDiff":
            example = {"filename": "p/test/model.odb", **example}
        if name == "XYCurveData1":
            example["loadcaseName"] = "Step-1"
        operations.append({"name": name, "title": title.split("(")[0].strip(),
                           "fields": list(fields.values()), "example": {"name": name, "args": example},
                           "sourceLine": text[:text.index("## " + title)].count("\n") + 1,
                           "uncertain": name in ("geometryParams", "modelDiff")})
    excel = []
    workbook = openpyxl.load_workbook(xlsx_path, data_only=True)
    for sheet in workbook:
        for i, row in enumerate(sheet.iter_rows(values_only=True), 1):
            if i == 1 or not any(x is not None for x in row):
                continue
            excel.append({"sheet": sheet.title, "row": i, "number": row[0], "path": row[1],
                          "purpose": row[2], "title": row[3], "request": row[4]})
    workbook.close()
    return {"sources": [{"file": str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p),
                          "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                         for p in (md_path, xlsx_path)],
            "operations": operations, "excelRows": excel}


def field_schema(field):
    kind = field["type"].lower()
    name = field["name"]
    if "array" in kind or "[][]" in kind or "arra<" in kind:
        schema = {"type": "array", "items": {}}
    elif kind in ("integer", "interger"):
        schema = {"type": "integer"}
    elif kind in ("number", "float"):
        schema = {"type": "number"}
    elif kind in ("boolean", "bool"):
        schema = {"type": "boolean"}
    elif kind == "object":
        schema = {"type": "object", "additionalProperties": True}
    else:
        schema = {"type": "string"}
    if name in ("nodeIds", "elemIds", "stateIds", "colors", "transparentEntities", "scalarEntities"):
        schema = {"type": "array", "items": {"type": "integer"}}
    if name == "nodePosList":
        schema = {"type": "array", "items": {"type": "array", "items": {"type": "number"},
                                             "minItems": 3, "maxItems": 3}}
    if name in ("box", "range"):
        size = 6 if name == "box" else 2
        schema = {"type": "array", "items": {"type": "number"}, "minItems": size, "maxItems": size}
    if name == "coordSystemId":
        schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    if name in ("rdb1", "rdb2"):
        schema = {"type": "object", "required": ["rdbPath"],
                  "properties": {"rdbPath": {"type": "string"}}}
    schema["description"] = field["description"] + (
        "；示例/约定：" + field["exampleText"] if field.get("exampleText") else "")
    return schema


def build_openapi(catalog):
    schemas = {"Envelope": {"type": "object", "required": ["code", "data"],
                            "properties": {"code": {"type": "integer", "description": "数巧原生成功码为 0"},
                                           "data": {"description": "随 name 变化，详见 API-reference.md；保留原始返回"},
                                           "message": {"type": "string"}}}}
    refs, examples = [], {}
    for op in catalog["operations"]:
        props = {f["name"]: field_schema(f) for f in op["fields"]}
        required = [f["name"] for f in op["fields"] if f["required"]]
        # Source has no formal property types for several inherited fields; use examples as hints.
        for k, v in op["example"]["args"].items():
            if k not in props:
                props[k] = {"type": "array", "items": {}} if isinstance(v, list) else {"type": "string"}
        args = {"type": "object", "properties": props, "additionalProperties": True}
        if required:
            args["required"] = required
        name = op["name"]
        schemas[name] = {"type": "object", "required": ["name", "args"],
                         "properties": {"name": {"type": "string", "enum": [name]}, "args": args},
                         "description": op["title"] + ("；源文档存在歧义，见 README" if op["uncertain"] else "")}
        refs.append({"$ref": "#/components/schemas/" + name})
        examples[name] = {"summary": op["title"], "value": op["example"]}
    response = {"description": "HTTP 成功仍需检查业务 code；原生 code=0 成功",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Envelope"},
                                                 "example": {"code": 0, "data": {}, "message": "success"}}}}
    query = {"summary": "数巧 DATA-API（按 name 分发）", "operationId": "simrightQuery",
             "tags": ["Markdown 原生接口"],
             "description": "共享同一 POST 地址，用 name 区分操作；各操作示例可作为接口用例导入。filename 为 RDB 相对目录。",
             "requestBody": {"required": True, "content": {"application/json": {
                 "schema": {"oneOf": refs, "discriminator": {"propertyName": "name"}}, "examples": examples}}},
             "responses": {"200": response}}
    paths = {MD_PATH: {"post": query}}
    excel_examples = {}
    for row in catalog["excelRows"]:
        if row["title"]:
            payload = json.loads(row["request"].replace("\\n", "\n"))
            excel_examples["row%d" % row["row"]] = {"summary": row["title"], "value": payload}
    paths[EXCEL_PATH] = {"post": {"summary": "Excel 部署的数据查询入口", "operationId": "simrightExcelQuery",
        "tags": ["Excel 部署接口"], "description": "使用 fileName；不得自动与原生路径混用。",
        "requestBody": {"required": True, "content": {"application/json": {
            "schema": {"type": "object", "required": ["name", "args"], "properties": {
                "name": {"type": "string"}, "args": {"type": "object", "required": ["fileName"],
                    "properties": {"fileName": {"type": "string"}}, "additionalProperties": True}}},
            "examples": excel_examples}}}, "responses": {"200": response}}}
    for path, method, props, required, desc in (
        ("/api/task/submit", "post", {"modelPath": {"type": "string"}, "uuid": {"type": "string"}},
         ["modelPath", "uuid"], "Excel 仅给出地址与请求体；POST 为待确认约定，响应/完成状态未定义"),
        ("/api/task/query", "get", {}, [], "Excel 仅给出 uuid=1-0；GET 为待确认约定，响应/完成状态未定义"),
    ):
        op = {"summary": "提交模型转换任务（方法待确认）" if method == "post" else "查询模型转换任务（方法待确认）",
              "operationId": "submitConversion" if method == "post" else "queryConversion",
              "tags": ["Excel 转换接口"], "description": desc, "x-method-unverified": True,
              "responses": {"200": {"description": "未提供响应协议，原样保留 JSON", "content": {"application/json": {"schema": {}}}}}}
        if method == "post":
            op["requestBody"] = {"required": True, "content": {"application/json": {
                "schema": {"type": "object", "required": required, "properties": props},
                "example": {"modelPath": "/home/file/a.odb", "uuid": "1-0"}}}}
        else:
            op["parameters"] = [{"name": "uuid", "in": "query", "required": True, "schema": {"type": "string"}, "example": "1-0"}]
        paths[path] = {method: op}
    paths["/applications/3dlite/create_session"] = {"post": {
        "summary": "创建多窗口 Session（方法待确认）", "operationId": "createSession",
        "tags": ["其他接口"], "x-method-unverified": True,
        "description": "原文未声明 HTTP 方法；POST 为待确认约定。请求表与 scenes 示例有差异；成功示例 code=1。",
        "requestBody": {"required": True, "content": {"application/json": {"schema": {
            "type": "object", "required": ["viewLayout"], "properties": {
                "viewLayout": {"type": "integer", "minimum": 1, "maximum": 12},
                "scenes": {"type": "array", "items": {"type": "object", "properties": {
                    "id": {"type": "string"}, "path": {"type": "string"}}}}}},
            "example": {"viewLayout": 1, "scenes": [{"id": "1", "path": "p/test/model.odb"}]}}}},
        "responses": {"200": {"description": "原文示例 code=1, id 为 sessionId, msg 为提示信息",
            "content": {"application/json": {"schema": {"type": "object", "properties": {
                "code": {"type": "integer"}, "id": {"type": "string"}, "msg": {"type": "string"}}}}}}}}}
    return {"openapi": "3.0.3", "info": {"title": "数巧 SimRight 仿真数据接口", "version": "1.0.0",
            "description": "依据本地 DATA-API.md 与接口功能说明 Excel 整理；差异及未验证项见 README。投影是本地计算，不虚构服务端投影路由。"},
            "servers": [{"url": "http://localhost:8080", "description": "占位地址，导入后替换实际服务"}],
            "paths": paths, "components": {"schemas": schemas}}


def postman_request(title, payload, path, *, method="POST", description=""):
    request = {"method": method, "header": [{"key": "Content-Type", "value": "application/json"}],
               "url": "{{baseUrl}}" + path, "description": description}
    if payload is not None:
        request["body"] = {"mode": "raw", "raw": json.dumps(payload, ensure_ascii=False, indent=2),
                           "options": {"raw": {"language": "json"}}}
    return {"name": title, "request": request, "response": []}


def build_postman(catalog):
    md = []
    for op in catalog["operations"]:
        payload = copy.deepcopy(op["example"])
        if "filename" in payload["args"]:
            payload["args"]["filename"] = "{{filename}}"
        md.append(postman_request(op["title"] + " / " + op["name"], payload, MD_PATH,
            description="DATA-API.md 第 %d 行；%s" % (op["sourceLine"],
                "需要确认文档歧义" if op["uncertain"] else "原生接口；业务成功码为0")))
    excel = []
    for row in catalog["excelRows"]:
        if row["title"]:
            payload = json.loads(row["request"].replace("\\n", "\n"))
            payload["args"]["fileName"] = "{{uuid}}"
            excel.append(postman_request(row["title"], payload, EXCEL_PATH,
                description="Excel %s 第%d行。变量拼写按原样保留；E.EL.Midrincipal 疑似笔误，先查 variables。" % (row["sheet"], row["row"])))
    excel += [postman_request("提交模型转换（POST 方法待确认）", {"modelPath": "{{modelPath}}", "uuid": "{{uuid}}"}, "/api/task/submit"),
              postman_request("查询模型转换（GET 方法待确认）", None, "/api/task/query?uuid={{uuid}}", method="GET")]
    tensor = []
    for kind, prefix in (("应力", "S.EL."), ("应变", "E.EL.")):
        for component in ("11", "22", "33", "12", "13", "23"):
            payload = {"name": "XYCurveData1", "args": {"filename": "{{filename}}", "nodeIds": [1],
                       "loadcaseName": "{{loadcaseName}}", "xVarName": "var_time", "yVarName": prefix + component,
                       "coordSystemType": 1, "stateIds": [1, 2]}}
            tensor.append(postman_request(kind + component + "（投影输入）", payload, MD_PATH,
                description="变量名须以 variables 返回为准。六次请求须保持坐标系、工况、帧、平均与壳层一致；投影由 Python 本地计算。"))
    return {"info": {"name": "数巧 SimRight：文档接口、Excel 功能与六分量采集",
                     "description": "两个部署分组保持各自路径与参数拼写。删除/探针请求在手动执行时发出。",
                     "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "variable": [{"key": k, "value": v} for k, v in {
                "baseUrl": "http://localhost:8080", "filename": "p/test/model.odb", "uuid": "1-0",
                "modelPath": "/home/file/a.odb", "loadcaseName": "Step-1"}.items()],
            "item": [{"name": "Markdown 原生接口", "item": md}, {"name": "Excel 功能与转换", "item": excel},
                     {"name": "方向投影的六分量采集", "item": tensor},
                     {"name": "其他接口", "item": [postman_request("创建 Session（POST 方法待确认）",
                         {"viewLayout": 1, "scenes": [{"id": "1", "path": "{{filename}}"}]},
                         "/applications/3dlite/create_session", description="成功示例 code=1，不使用 DATA-API code=0 判断")]}]}


def build_reference(catalog):
    lines = ["# 数巧接口参数索引", "", "由 `python -m tools.simright.generate_artifacts` 生成。来源摘要见 source-catalog.json。",
             "", "共同入口：`POST /applications/3dlite/api/v1/query`。原生成功码为 `0`。",
             "", "请求体为 `{\"name\": 操作名, \"args\": 参数对象}`。下表保留来源说明；例子已修正原文 JSON 语法。",
             "", "视图公共参数和 resultOptions 参数直接放在 args 中，与其他参数平级。",
             "", "响应统一外层 `code/data/message`，data 的对象/数组结构随操作变化，不能统一取 data.value。",
             "", "| 操作 | data 主要结构（依据原文） |", "| --- | --- |",
             "| nodeId | `[{nodeId,nodePos,stateId,...}]`；示例不含 partId |",
             "| nodeInfo / elementInfo | `[{nodeId或elemId,partId,stateId,values,...}]`；values 可为数组 |",
             "| XYCurveData1 | `[{nodeId,data:[[x,y],...],xVarName,yVarName}]` |",
             "| probeGroupByPos | `{sprobes:{probes:[...],groups:[...]}}` |",
             "| loadcases / freqValue | `{value:[...]}` |",
             "| variables | `{variables:[...]}` |",
             "| assemble | `{root,assms,parts:[{id,oriId,name,...}],sets}` |",
             "| extremeValue | `{nodeId,nodePos,partId,oriId,stateId,value}` |",
             "| contourImage / modelImage / animationGif / freqGif | `{value:Base64字符串}` |",
             "| curveInfo / curveData | 树形数组 / 含 values 的数组 |",
             "| hitEntities | `[{partids:[...],oriIds:[...]}]` |",
             "| measureValue | 数值或数组，原文示例为数组 |",
             "| deleteModelFile | 字符串 |",
             "| modelDiff / geometryParams | 原文存在矛盾，见 README |", ""]
    for op in catalog["operations"]:
        lines += ["## " + op["name"] + " — " + op["title"], "",
                  "来源：DATA-API.md 第 %d 行。" % op["sourceLine"], "",
                  "| 参数 | 类型 | 必填 | 说明 | 示例/约定 |", "| --- | --- | --- | --- | --- |"]
        for f in op["fields"]:
            lines.append("| %s | %s | %s | %s | %s |" % (f["name"], f["type"], "是" if f["required"] else "否",
                f["description"].replace("|", "\\|"), f.get("exampleText", "").replace("|", "\\|")))
        lines += ["", "```json", json.dumps(op["example"], ensure_ascii=False, indent=2), "```", ""]
    lines += ["## Excel 功能对应", "", "查询入口：`POST /applications/3dlite/api/v1/query/query`，模型参数为 `fileName`。", "",
              "| 行号 | 功能 | 请求 |", "| --- | --- | --- |"]
    for row in catalog["excelRows"]:
        lines.append("| %d | %s | `%s` |" % (row["row"], row["title"] or row["purpose"],
                   row["request"].replace("\\n", " ").replace("|", "\\|")))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    parser.add_argument("--excel", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    catalog = parse_sources(args.markdown.resolve(), args.excel.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    dump(args.output / "source-catalog.json", catalog)
    dump(args.output / "simright.openapi.json", build_openapi(catalog))
    dump(args.output / "simright.postman_collection.json", build_postman(catalog))
    (args.output / "API-reference.md").write_text(build_reference(catalog), encoding="utf-8")
    print("Generated %d DATA-API operations and %d Excel rows in %s" %
          (len(catalog["operations"]), len(catalog["excelRows"]), args.output))


if __name__ == "__main__":
    main()
