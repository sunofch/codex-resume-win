"""用标准输入输出模拟分页协议，不读取用户数据。"""
import json
import sys

mode = sys.argv[1]
if mode == "exit":
    print("模拟启动错误", file=sys.stderr)
    sys.exit(1)
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    if request["method"] == "initialize":
        result = {}
    elif mode == "error":
        print(json.dumps({"id": request["id"], "error": {"message": "模拟接口错误"}}), flush=True)
        continue
    else:
        params = request["params"]
        assert params["currentAccountOnly"] is False
        assert params["modelProviders"] == []
        second = "cursor" in params
        result = {"data": [{"id": "a", "updatedAt": 1}, {"id": "b", "updatedAt": 2}] if second else [{"id": "a", "updatedAt": 1}],
                  "nextCursor": "same" if mode == "loop" or not second else None}
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
