# mediation

调解工作台 MVP，包含：

- FastAPI 后端
- DeepSeek 大模型接入
- 腾讯云实时语音识别 provider
- 前端单页工作台

## 目录

- `backend/`：Python 后端服务
- `frontend/`：静态页面

## 运行

```bash
cd mediation/backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000
```

## 环境变量

参考 `backend/.env.example`。

