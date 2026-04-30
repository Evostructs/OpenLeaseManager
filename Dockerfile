FROM node:20-alpine AS frontend-builder

WORKDIR /frontend
ARG VITE_APP_TITLE=租赁合同管理系统
ARG VITE_APP_SUBTITLE=租赁运营后台
ARG VITE_APP_MARK=LM
ENV VITE_APP_TITLE=$VITE_APP_TITLE
ENV VITE_APP_SUBTITLE=$VITE_APP_SUBTITLE
ENV VITE_APP_MARK=$VITE_APP_MARK
COPY frontend/package.json ./package.json
RUN npm config set registry https://registry.npmmirror.com \
    && npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY app ./app
COPY --from=frontend-builder /app/frontend_dist ./app/frontend_dist

ENV DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
