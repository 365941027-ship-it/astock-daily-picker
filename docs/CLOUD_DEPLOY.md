# 云端化部署说明（GitHub Actions 可选）

## 为什么默认仍在本机自动更新

每日流水线依赖：
- 已缓存的数百只个股日K（本地 `daily_picker/cache/`，约数百 MB，未提交到公开仓库）；
- 本机 Clash 代理（`socks5h://127.0.0.1:7897`）访问东财/新浪行情；
- 历史回放从 `2026-07-01` 重算需要全量缓存。

这些依赖决定了“直接搬进 GitHub Actions”不能开箱即用，必须先在远端重建缓存并验证行情源可达性。

## 若要迁移到 GitHub Actions（推荐步骤）

1. 把行情缓存上传到私有对象存储（如 GitHub Packages / 私有仓库 release / S3），Actions 每次开始先下载、结束后回传增量。
2. 在仓库 Settings → Secrets 配置：
   - `ASTOCK_CACHE_DOWNLOAD_URL` / `ASTOCK_CACHE_UPLOAD_URL`
   - `ASTOCK_PUSH_WEBHOOK`（推送完成通知）
   - `GH_PAT`（若 Pages 部署走自定义 token）
3. 在 Actions 中于每个交易日 18:05（UTC+8）运行 `daily_update.py`，并复刻 `publish_ghpages.sh` 的部署步骤。
4. 确认 Actions 所在区域能访问东财/新浪；若不能，需保留本机跑数据 + 远端只发布静态文件的混合方案。

## 当前状态

为不引入无法验证的定时任务，仓库暂不含可执行 workflow。需要迁移时，先用一次 `workflow_dispatch` 做连通性与全量缓存测试，通过后再启用 schedule。
