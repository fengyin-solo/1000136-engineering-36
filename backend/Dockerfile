FROM python:3.12-slim
WORKDIR /srv/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# 构建期预检：源文件缺失或口径错误时直接让镜像构建失败，而不是带病上线
RUN python -m app.prepare_data
EXPOSE 8000
# 启动期再次准备（缓存有效则复用），失败信息进容器日志
CMD ["sh", "-c", "python -m app.prepare_data && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
