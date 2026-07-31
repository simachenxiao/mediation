# mediation

调解工作台 MVP。

## 目录

- `backend/`：FastAPI 后端
- `frontend/`：单页前端

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

## 配置

参考 `backend/.env.example`。

- `ASR_MODE=realtime`
- `ASR_MODE=realtime_diarization`

其余填写腾讯云和 DeepSeek 的实际账号信息即可。
