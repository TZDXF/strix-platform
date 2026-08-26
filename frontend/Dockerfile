FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# 运行阶段用 Debian 版基础镜像：CentOS 7 老内核 + 老 libseccomp 环境下，
# alpine 新版用户态会因 seccomp 对未知系统调用返回 EPERM 而无法启动
FROM nginx:1.26
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
