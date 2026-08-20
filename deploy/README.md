# 公网部署：让所有人（含大陆手机）通过链接访问

网页版默认只监听本机（127.0.0.1:8235）。要让别人在手机上直接打开，需要把服务放到公网。按下面任一方案操作，**推荐方案 A**。

## 方案 A：国内云服务器（推荐）

服务代码会连东方财富/新浪的公开行情接口（国内直连即可，无需代理），所以放在国内服务器上最稳。

1. 买一台国内云服务器（阿里云/腾讯云轻量应用服务器即可，2 核 2G 够用），系统选 Ubuntu 22.04。
2. 把项目上传到服务器 `/opt/ashare-picker`：

   ```bash
   scp -r /Users/yexiyan/Documents/A股复盘 user@服务器IP:/opt/ashare-picker
   ```

3. 用 systemd 跑起来（不需要 Docker）：

   ```bash
   sudo apt update && sudo apt install -y python3 python3-pip
   sudo pip3 install python-docx
   sudo cp /opt/ashare-picker/deploy/ashare-picker.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now ashare-picker
   ```

   或用 Docker：

   ```bash
   cd /opt/ashare-picker && docker build -f deploy/Dockerfile -t ashare-picker .
   docker run -d --name ashare-picker -p 8235:8235 --restart always ashare-picker
   ```

4. 在云服务器控制台**安全组放行 TCP 8235 端口**（或 80/443）。
5. 访问 `http://服务器IP:8235` 即可。建议再配一个域名 + HTTPS（国内服务器域名需要 ICP 备案）。

> 定时任务：服务器上每天 18:05 自动更新数据，用 crontab：
>
> ```bash
> 5 18 * * 1-5 cd /opt/ashare-picker && /usr/bin/python3 daily_update.py --date $(date +%F) >> /opt/ashare-picker/daily.log 2>&1
> ```
>
> 邮件推送在服务器上同样可用（配置好 SMTP 授权码后加 `--send-email --to 收件邮箱`）。

## 方案 B：内网穿透（本机不关机时可用）

不买服务器，把本机服务暴露到公网。大陆手机能访问，需要选择有国内节点的穿透服务（如 cpolar、花生壳）。

以 cpolar 为例：

1. 到 cpolar 官网注册并安装客户端（macOS 版）。
2. 登录后拿 authtoken 并绑定：

   ```bash
   cpolar authtoken 你的token
   ```

3. 启动服务并建立公网隧道：

   ```bash
   # 终端 1：启动网页版（用启动选股网页版.command 或下面的命令）
   python webapp.py --host 0.0.0.0 --port 8235

   # 终端 2：建立公网隧道
   cpolar http 8235
   ```

4. cpolar 会给出一个公网地址（如 `https://xxxx.cpolar.cn`），把该链接发给任何人，手机浏览器直接打开。

注意事项：
- 免费版隧道域名会变化，重启后要重新发链接；
- 本机需要保持开机且服务运行；
- 若想要固定域名，升级 cpolar 付费套餐。

## 方案 C：部署到微信/支付宝小程序？

当前网页版是纯网页应用，不需要小程序框架。若以后想要“小程序壳”，可以在云服务器配好 HTTPS 后，用 WebView 套壳或对接企业微信，成本较高，暂不建议。

## 安全提示

- 网页只读公开行情和本地缓存，不涉及用户数据，公网部署风险低；
- 若担心被刷，可在 Nginx/防火墙层面加 IP 白名单或简单访问口令（webapp.py 目前不带登录，需要时可加）；
- 报告里的股票观察内容仅供个人研究，公开链接请自行评估传播风险。
