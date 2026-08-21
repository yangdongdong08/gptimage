# 干净源码包使用说明

这个目录只包含源码、测试和通用部署配置，不包含制作者的账号、Token、邮箱、日志、数据库、服务器地址或 `.env`。

## 推荐部署方式

1. 复制环境变量模板：

   ```bash
   cp .env.example .env
   ```

2. 为每次部署生成独立的管理密钥，并填写到 `.env` 的 `CHATGPT2API_AUTH_KEY`：

   ```bash
   openssl rand -hex 32
   ```

3. 注册功能推荐使用带 WARP、Privoxy 和 FlareSolverr 的本地构建配置：

   ```bash
   docker compose -f docker-compose.warp.yml up -d --build
   ```

   不需要代理组件时可使用：

   ```bash
   docker compose up -d --build
   ```

4. 默认访问地址是 `http://服务器地址:3000/`。登录后台后，使用接收方自己的邮箱服务配置注册机；源码包没有预置任何邮箱服务凭据。

5. 查看状态：

   ```bash
   docker compose -f docker-compose.warp.yml ps
   curl http://127.0.0.1:3000/health?format=json
   ```

## 注册流程说明

当前注册逻辑使用 passwordless signup：发送邮箱验证码、校验验证码、创建账号并换取 access/refresh token。新注册账号没有可用的随机密码，保存的 `password` 为空，后续主要依赖 refresh token。

## 再次分发前

不要打包运行后生成的 `.env`、`config.json`、`data/`、数据库或日志。它们会包含部署密钥、邮箱配置或账号 Token，并已被 `.gitignore` 和 `.dockerignore` 排除。
