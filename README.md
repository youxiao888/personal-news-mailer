# 四刊个人目录与邮件工作流

这是可以直接提交到 GitHub 仓库的 Python 项目。它独立于 ChatGPT/OpenAI 运行，不需要 OpenAI API、Brave API、服务器或付费数据库。默认每小时尝试采集 The Economist、NYT、FT、WSJ 的公开目录，每天将链接目录和有限数量的提取文本发到自己的邮箱。

**交付的是源码与本地验证结果，还没有替你创建 GitHub 仓库、配置账号或实际发邮件。** 正文提取依赖第三方工具与网站当时的访问条件，不能保证取得付费全文，也不能保证四家目录全覆盖。请先阅读 [核实记录](VERIFICATION.md)，特别是最新访问诊断和未解决的人机验证。

## 最新访问修复（2026-10-16）

- WSJ 目录此前为 0，是本项目把 RSS 域名的 robots.txt 403 当成整站禁止抓取。已按 RFC 9309 修正：普通 4xx 表示规则不可用；429、5xx、网络失败仍延后，明确 Disallow 仍遵守。实测 7 个官方 RSS 正常，7 天窗口得到 293 条唯一链接。不能由此推算全覆盖。
- 加入 curl_cffi 浏览器兼容请求，再调用 bpc-fetch 提取器；失败时在同一超时预算内回退原 CLI。本地 FT / WSJ 样本分别取得 808 / 1,347 字符候选文本，页面带付费标记，均标为 `partial_or_paid_unverified`。订阅套餐页和验证页被拒绝，不冒充文章。
- Economist / NYT 样本仍遭人机验证。普通真实浏览器也未通过；没有证据证明改用 GitHub 出口即可解决，亦没有验证到四站付费全文。
- 架构继续保持纯 GitHub Actions，不需要用户电脑常开。先用下面的无凭证测试核验实际 runner，再决定是否启用邮件。

### 先在 GitHub 实测访问（不需任何 Secret）

上传后打开 **Actions → Access check (no email) → Run workflow**。它会启动 Chromium 验证安装、采集最新目录、每家选一条最新链接运行修复后的提取流程。运行 Summary 和 `access-report-运行ID` 附件记录来源状态与文本分类；不上传正文、不读写正式发送状态、不发送邮件。绿勾只代表诊断完成，不代表取得完整文章。人工验证仍失败时报告会如实保留，不能据此宣称自动访问已解决。

## 1. 先理解实际能得到什么

| 功能 | 实际行为与边界 |
| --- | --- |
| 每小时目录采集 | UTC 每小时第 17 分钟触发。合并所有配置的 RSS、栏目页、期刊页、robots 中声明的站点地图。每个来源单独记录状态。 |
| 目录校验 | 限定出版社域名、过滤明显栏目路径、规范化链接、合并多来源和标题更新。RSS/地图中出现只证明来源列出过该链接，不代表文章页已成功打开。 |
| 去重 | 去掉查询参数与锚点，合并 www/mobile/AMP 常见变体；WSJ 有末尾文章 ID 时按 ID 合并。没有全站唯一 ID 的异名转载或改路径可能仍重复。 |
| 回溯 | 默认 7 天滚动窗口。每次重新读来源，重新访问近期站点地图，并轮换其余子地图。漏跑以后重新采集能补回仍在来源中的条目；已从所有来源消失的链接无法凭空补回。 |
| 日期 | 分开记录发布时间、来源 lastmod、首次/最后发现时间；URL 日期只有日精度并标为 UTC 假定。lastmod 永远不冒充发布时间。无日期记录保留“未知”。 |
| 正文 | 每份日报最多尝试 12 篇，总处理时间上限 240 秒，单篇上限 45 秒。四家交错分配尝试机会。大部分条目可能只有链接。 |
| 正文重试 | 失败/过短文本最多尝试两次；18 小时后可在下一份日报标成“正文重试补发”。未轮到尝试但目录已发出的条目不会无限排队抓取。 |
| 质量判断 | HTTP 状态、工具 JSON、输出路径、文本长度、常见拦截词、内容哈希。最长正文保留 45,000 字符并标记本地截断；任何长度都不能证明全文完整。 |
| 每日邮件 | 默认洛杉矶时间 07:00 后第一个成功任务发“前一天及历史未发送”的目录。通常约 07:17，可能延迟。发布时间属于前一天的晨间回补也可纳入。 |
| Android 阅读 | 邮件正文为目录；`reading.html` 附件包含目录与提取文本，`catalogue.csv` 是完整目录，`health.json` 是本轮来源健康记录。HTML 无脚本、无远程图片；可保存后用浏览器离线打开。 |

Economist 的 weeklyedition/printedition 只是补充目录来源。此项目不是四家纸刊 PDF、电子报、官方期号档案，也没有把网页目录假称为“完整当日期刊”。多媒体、互动页、付费专栏、地区版本和新 URL 格式仍可能遗漏。

## 2. 核实过的 bpc-fetch

真实仓库是 [Sophomoresty/bpc-fetch](https://github.com/Sophomoresty/bpc-fetch)，本项目固定在提交：

```text
76e24f51f4bc7b1ed9bd91418796f3cf104be56b
```

项目声明版本 `0.1.0`，要求 Python >=3.11；本工作流使用 Python 3.12。上游 README 的 `github.com/user/bpc-fetch.git` 是占位地址，不能照抄。实际安装源码时保留 `data/sites.js`，并显式传入路径，避免 wheel 未携带根目录 data 文件所导致的问题。

以下选项已对照该提交的 CLI 源码，并实际运行 `--help` 验证：

```sh
bpc-fetch doctor --sites-js .runtime/bpc-src/data/sites.js --compact
bpc-fetch sites --filter economist --sites-js .runtime/bpc-src/data/sites.js --compact
bpc-fetch discover economist.com --since 7d --limit 500 --compact
bpc-fetch fetch "真实文章URL" --out-dir .runtime/articles --no-images --sites-js .runtime/bpc-src/data/sites.js --compact
bpc-fetch batch --file urls.txt --out-dir .runtime/articles --no-images --concurrency 2 --compact
```

`fetch` 没有 `--timeout`、`--format` 或 `--output`；本项目用子进程实现超时。`discover` 的默认 limit 是 20，且找到 RSS 后不再合并地图/首页；所以目录采集由本项目完成。上游的“支持 936 个站点”是站点表数量，不是成功率。它可能使用浏览器、请求头和第三方存档等回退策略；CLI 没有可依赖的“仅直接公开页面”开关。若不希望尝试这些策略，把 `config.json` 中 `bpc.enabled` 改为 `false`，目录与邮件仍可用。

本项目不带订阅 Cookie、不处理登录验证码、不提供付费账号，也不保证访问权限。请仅用于自己的阅读，并确认你对所访问/保存内容的使用权限。上游许可证和第三方代码仍归原作者，本项目没有把上游源码复制进来。

## 3. 零支出条件与额度

2026-10-16 核对的 [GitHub Actions 计费文档](https://docs.github.com/en/billing/concepts/product-billing/github-actions)：**公开仓库的标准 GitHub 托管 runner 免费**；GitHub Free 私有仓库每月包含 2,000 分钟、500 MB artifact 存储，缓存额度为每仓库 10 GB。大型 runner 始终收费。

此项目使用公开仓库 + `ubuntu-24.04` 标准 runner，未开启付费 runner、付费 API 或缓存。临时附件只在每天需要发邮件或手动预览时上传，保留 1 天且加密；元数据写入 `news-state` 分支。请遵守平台使用规则，并在 GitHub 的 Billing / Usage 查看自己账号的实际额度、预算和限制。不要为了本项目开启付费超额使用。

如果必须使用私有仓库：每月约 720–744 次小时任务，平均每次超过约 2.69 分钟就可能用尽 2,000 分钟，且每日正文安装、发送、手动重试与其他仓库也会消耗额度。这不是零费用保证；可以降低采集频率、关闭 BPC，并使用账号的零支出预算/超额阻止功能。

免费邮件默认用已有个人 Gmail，不需要买域名。备选 [Brevo 免费方案](https://help.brevo.com/hc/en-us/articles/208580669-FAQs-What-are-the-limits-of-the-Free-plan) 当前为每天 300 封，且包含 [SMTP 服务](https://developers.brevo.com/docs/smtp-integration)。Brevo 账号审核、发件人验证、域名认证与反滥用限制可能影响可用性；若被要求提供已认证域名而你没有域名，使用已有 Gmail，别为“零成本”方案额外买域名。服务条款与额度可能变化。

## 4. 部署：按顺序做

### 第一步：创建公开仓库并上传

1. 解压交付的 zip；进入 `news-mailer` 文件夹。
2. 在 GitHub 创建一个 **Public** 仓库，例如 `personal-news-mailer`。不要把邮箱密码写进 README 或配置。
3. 上传本文件夹里的全部源码，确保隐藏目录 `.github/workflows/` 也上传，并将 `main` 设为默认分支。不要上传外层文件夹使 workflow 多嵌套一级。
4. 仓库根目录应能直接看到 `README.md`、`config.json`、`newsflow/` 和 `.github/`。

电脑上也可使用 Git（将地址替换为你自己的新仓库）：

```sh
git init
git add .
git commit -m "Add personal news mailer"
git branch -M main
git remote add origin https://github.com/YOUR_NAME/personal-news-mailer.git
git push -u origin main
```

此处 `YOUR_NAME` 是明确的部署占位符，不是 bpc-fetch 上游地址。无需安装 Python 即可让 GitHub 执行；本地运行/解密才需要 Python。

仓库上传后请先运行上面的 **Access check (no email)**。即使未配置邮箱，也能先确认本站点访问是否满足你的阅读需求。

### 第二步：调整时间和采集范围

编辑根目录 `config.json`，模板副本为 `config.example.json`。默认采用本次会话时区 `America/Los_Angeles`、早晨 7 点；中国时间改为 `Asia/Shanghai`。时区使用 IANA 名称，会自动处理夏令时。

```json
"timezone": "Asia/Shanghai",
"send_after_local_hour": 7,
"lookback_days": 7
```

workflow 的 `17 * * * *` 维持不变，每小时触发后由 Python 判断本地日期与发送门槛。第一轮可能只是建立目录；没有符合前一天截日条件的记录时，日报可能为空，正文会明确显示 0 条。

来源模板含经过实测的来源和明确保留的失败候选地址；不能把“配置在文件里”理解为“当前可达”。失效源不会阻断其他来源，详见 health.json。新增来源需加入对应 `fetch_hosts` 白名单；不要加入不信任域名。初始载入包含最多 7 天回溯，因此首份邮件可能比后续日报大。

正文上限在 `bpc` 配置内。提高篇数、时间和地图预算会增加运行量、请求量与失败风险。默认不追求每条目录都下载正文。

### 第三步：准备免费 SMTP

**Gmail 路径：**

1. 给发件 Gmail 账户开启 [两步验证与应用专用密码](https://support.google.com/accounts/answer/185833)。
2. 新建一个本项目专用的应用密码，记录后直接存进 GitHub Secret。用应用密码，别用正常登录密码。
3. 如果应用密码入口不可用，可能是组织账户、仅安全密钥的验证方式或 Advanced Protection 等限制；不要关闭两步验证来迁就脚本。改用支持 SMTP 的已有邮箱或 Brevo。
4. 默认服务器 `smtp.gmail.com`、587、STARTTLS；如果服务商提供 465，可用 `ssl`。所有连接都校验证书，禁止明文降级。

**Brevo 路径：** 免费注册并完成平台要求的激活/发件人验证，在 SMTP & API 页面取得 SMTP login 与 **SMTP key**，不是 API key。服务器 `smtp-relay.brevo.com`，建议 465 + `ssl`。`MAIL_FROM` 是已验证发件地址，SMTP login 不能直接冒充发件地址。详见 [官方配置说明](https://help.brevo.com/hc/en-us/articles/7924908994450-Send-transactional-emails-using-Brevo-SMTP)。

### 第四步：添加五个 Repository Secrets

仓库 **Settings → Secrets and variables → Actions → Secrets → New repository secret**：

| 名称 | 值 |
| --- | --- |
| `SMTP_USER` | Gmail 完整邮箱地址，或服务商 SMTP login |
| `SMTP_PASSWORD` | Gmail 应用密码（不含显示用空格），或服务商 SMTP key |
| `MAIL_FROM` | 一个合法发件邮箱；Gmail 建议与 SMTP_USER 相同 |
| `MAIL_TO` | 你的收件邮箱，单个地址；可以与发件邮箱相同 |
| `DIGEST_KEY` | 本地随机生成的 64 位十六进制密钥，用于临时附件加密 |

生成 DIGEST_KEY（在自己的电脑执行，结果只填入 Secret，不提交、不截图、不贴在聊天里）：

```sh
python -c "import secrets; print(secrets.token_hex(32))"
```

这是 32 字节随机值的十六进制表示，不能用自编的 64 位口令替代。备份在自己的密码管理器中，解密预览附件时需要。更换密钥后，旧附件不能用新密钥解密。

在同页面 **Variables** 中可选添加：

| 名称 | Gmail 默认值 | Brevo 示例 |
| --- | --- | --- |
| `SMTP_HOST` | `smtp.gmail.com` | `smtp-relay.brevo.com` |
| `SMTP_PORT` | `587` | `465` |
| `SMTP_SECURITY` | `starttls` | `ssl` |

未设置这三个 Variables 时自动用 Gmail 默认值。`.env.example` 仅供解释字段；程序不会自动读取 `.env`。地址也放 Secrets，以免邮箱显示在公开配置里。

### 第五步：允许工作流保存状态

在 **Settings → Actions → General** 确保允许官方 Actions 和 workflow 所需写入权限。YAML 仅给需要保存元数据的任务 `contents: write`；若组织策略禁止写权限，需要管理员放行或换你自己的仓库。

**不要保护 `news-state` 分支到禁止 bot 正常提交的程度。** 可以保护源码 `main`，但运行状态必须能写入。不要公开编辑状态分支、删除该分支或把它误设为默认分支。

### 第六步：先发 SMTP 测试

进入 **Actions → Four publications mailer → Run workflow**：

1. 选择默认分支 `main`。
2. `mode` 选 **smtp-test** 后运行；这会发一封短测试邮件，不发送文章，不改日报记录。
3. 检查收件箱和垃圾箱。日志的“accepted by SMTP”只证明 SMTP 接受，不证明最终入箱；必要时检查服务商投递记录。

### 第七步：预览采集，再启动日常运行

1. 再次 Run workflow，`mode` 选 **preview**。它会真实采集并保存目录，也可能按配置尝试 BPC，但不会发送日报，也不会标记文章已发。
2. 在该次运行的 Summary 观察各家新增数；进入 `news-state` 分支查看 `health.json` 与 `catalogue.json`。`new_unique` 是新发现链接数，不是当天全量出版数或覆盖率。
3. 下载该运行的 `digest-运行ID` artifact。里面是 `digest.enc` 和 `envelope.json`，都是加密封装，不能直接在手机当 HTML 打开。若需本地查看，按下节解密。
4. `mode` 选 **run** 可执行正常采集/发送门槛判断；它不会强制越过每日发送时间或重复发送同一天。
5. 确认访问测试与 SMTP 测试符合需要后，在 Repository Variables 添加 `NEWS_ENABLED=true`，启用每小时任务。默认未设置时定时采集/邮件不运行，方便先测试；手动模式仍可运行。后续无需打开 ChatGPT、浏览器或你的电脑。首次部署当日早晨之后建立的未知日期记录通常在次日发出。

### 下载预览并解密（可选）

把 artifact 解压到源码目录中的 `.runtime/sealed/`。需要本机 Python 3.12+ 和 PATH 中的 OpenSSL。Windows 可使用已有的 Git/OpenSSL 环境；Ubuntu runner 已提供 OpenSSL。

PowerShell 中安全读取密钥，然后生成本地预览：

```powershell
$newsDigestSecret = Read-Host 'DIGEST_KEY' -AsSecureString
$env:DIGEST_KEY = [System.Net.NetworkCredential]::new('', $newsDigestSecret).Password
python -m newsflow.seal unpack
Remove-Item Env:DIGEST_KEY
python -m newsflow.mail preview
```

`mail preview` 还需要对应 `news-state` 中的 `catalogue.json` 放在 `.runtime/state/`，用来核对链接。若只查看解密后的数据，可跳过它，直接生成 HTML：

```sh
python -c "from newsflow.common import load; from newsflow.digest import write_bundle; write_bundle('.runtime/bundle', load('.runtime/bundle/bundle.json'))"
```

打开 `.runtime/bundle/reading.html`。这些运行文件已被 `.gitignore` 排除，别强制提交。正式邮件不需要用户做解密操作，发送任务会完成解密。

## 5. 邮件、安全和恢复

**持久化：** `news-state` 是独立的元数据分支，只有 `catalogue.json`、`ledger.json`、`health.json`。不靠缓存或 artifact 来维持去重，冷启动不会因为缓存过期而重发。状态分支公开可见标题、链接、时间和正文处理状态；不包含 SMTP 值、收件邮箱或文章正文。若连这些阅读偏好元数据也不能公开，使用私有仓库并接受其额度约束。

**正文临时存储：** runner 的临时目录在任务结束后随托管环境回收；上传的 artifact 只有加密后的 JSON。加密采用 OpenSSL AES-256-CBC + PBKDF2，并对密文做独立密钥 HMAC-SHA256 校验，先验签再解密。保留期 1 天，不应视为长期备份。发送任务重新生成 HTML，不执行 artifact 中的文件或脚本。正文与标题均经过 HTML 转义，无远程跟踪图。

**凭证隔离：** 采集/BPC 任务没有 SMTP secrets；发送任务不安装、不导入 bpc-fetch。源码和官方 Actions 固定提交，第三方运行依赖固定版本；这仍不是第三方源码的完整安全审计。仅允许可信人员改源码或 workflows。不要打开 shell debug、SMTP debug，不要把 Secret 放入变量、命令行、URL、日志、issue 或任何提交。应用密码/SMTP key 泄露时立即撤销换新；删掉最新文件不等于清除了 Git 历史。

**发送状态：** 认证、生成 MIME、尺寸校验完成后，先将 `pending` 写入 Git，再发送 SMTP DATA，成功后提交 `sent`。如果第一次写入失败，不发邮件；如果发送途中掉线，或邮件已被接受但第二次状态提交失败，保留 `pending`，停止自动重发。SMTP 没有跨 Git 和邮箱的事务，因此不承诺 exactly-once。账本记录的 Message-ID 能辅助排查，也不能保证收件服务器去重；授权重试生成新批次时 ID 可能改变。

处理 pending：

1. 查看 `news-state/ledger.json` 中待核查的 `target` 日期和 Message-ID，检查发件服务商日志与收件箱。
2. 确认已收到：手动运行 `mode=resolve`，`resolve_date` 填该日期，`resolve_action=mark-sent`。
3. 确认未发送或愿意承担重复风险：运行 `resolve_action=allow-retry`。随后运行 `mode=run` 或等下次定时任务，未发目录会被重新纳入最新日报。它可能不是原批次的逐字重放，原邮件可能已投递。
4. 同一天已经 sent 的日报正常重跑会跳过；想阅读旧邮件请在邮箱查找。没有提供任意覆盖 sent 的按钮，以免误操作。

修改凭证前就失败、或未进入 SMTP DATA 前失败，通常可以修好配置后正常重跑。遇到公共日志只显示错误类型，这是为了防止服务器回显邮箱/凭证；先核对 Secret 名称、端口、TLS 模式和服务商日志。

## 6. 运维与常见问题

| 现象 | 处理 |
| --- | --- |
| WSJ 为 0 | 确认部署包含本次 robots 4xx 修复；本地已验证 7 个 RSS 可用。查看实际 runner 的 health.json，不能把 0 解释为当天没出版。 |
| `ok_sources=0` | 来源或 robots 不可达/拒绝。其他出版社仍运行；同样检查 Actions 是否失败，不凭空补文章。 |
| 收到目录但正文很少 | 正常边界：12 篇/240 秒预算、付费墙、短页、脚本、网络、浏览器或上游提取失败都可能如此。 |
| `partial_or_paid_unverified` | 页面标记为付费内容，仅获得部分或完整性未知的文本，不能算付费全文。 |
| `blocked_challenge` | 访问要求人机验证；纯 GitHub Actions 尚无已验证解决方案。 |
| `candidate_text_unverified` | 产生了候选文本，未核实全文；可能仍是摘要、音频介绍或其他片段。 |
| `short_or_wall_unverified` | 文本太短或含疑似拦截提示；不称为成功全文。 |
| `not_attempted` | 受预算、关闭 BPC 或未轮到处理影响，仅有目录；不是正文失败的统计样本。 |
| `failed` | 工具返回失败、超时、输出无效或路径不安全；最多按配置重试。 |
| 任务不按整点到达 | GitHub 定时任务可能延迟，繁忙时甚至丢弃；不是 SLA 调度器。这里避开整点，使用 7 天回溯降低影响。 |
| 运行 60 天后停止 | GitHub 文档说明公开仓库无活动 60 天会停用 schedule。状态分支提交不要当作永久保活保证；定期检查 Actions，按需要重新启用。项目不伪造源码提交来保活。 |
| 首封目录特别多 | 第一次包含回溯窗口。目录不偷偷截前 N 条；邮件较大时正文简化、完整目录留在附件。序列化邮件超过 9 MB 会失败，不静默丢条目。 |
| 需要扩展首次历史 | 可以调 lookback_days，但无法保证来源仍保留历史目录；不会通过搜索生成看似真实的 URL。 |
| 状态分支 push 失败 | 检查权限、分支保护、并发写入与 GitHub 状态。系统不 force push，不用空状态覆盖网络失败。 |
| 想暂停 | 在 Actions 页面 Disable workflow；不用删除状态分支。恢复后回溯窗口内仍可补采。 |

GitHub 的 concurrency 只保留有限排队任务，繁忙时中间触发也可能被替换；代码通过重复读取与时间窗口缓解，并不保证每小时恰好执行一次。来源 health 保留最近 72 次采集记录；站点地图 budget/轮换会明确记录本轮未扫描数量，不能由此计算未知总量的覆盖率。

目录与发送账本目前不自动删除，长期运行会增大 Git 历史。定期备份状态、检查仓库体积与存储使用；需要整理历史时先停 workflow、做备份，再维护，避免丢失去重与发送记录。

## 7. 本地检查与文件说明

核心流程只有 Python 标准库依赖。Linux runner 自带时区数据；Windows 本地可执行 `python -m pip install -r requirements.txt` 安装时区数据。

```sh
python -m unittest discover -s tests -v
python -m newsflow.collect --state .runtime/state
python -m newsflow.digest --force
python -m newsflow.mail preview
```

本地 collect 不需要 GitHub token；只有 state_io 的远端读写需要工作流中的临时 GITHUB_TOKEN。`mail preview` 不连接 SMTP；`mail send` 是 Actions 的正式发送入口，不能把预览误当为已部署。

| 文件 | 用途 |
| --- | --- |
| `.github/workflows/news.yml` | 每小时采集、每日发送、预览、SMTP 测试与 pending 恢复 |
| `.github/workflows/access-check.yml` | 无邮箱凭证的实际 runner 访问测试，只保存诊断元数据 |
| `.github/workflows/check.yml` | push main / pull request 的离线检查，不接收 SMTP secrets |
| `newsflow/collect.py` | 来源解析、请求限速、robots、域名白名单、地图遍历、目录合并 |
| `newsflow/compat.py` | 浏览器兼容请求、验证页/订阅页拒绝、付费片段标记 |
| `newsflow/bpc.py` | 固定 CLI 选项、子进程超时、正文候选状态 |
| `newsflow/digest.py` | 本地日期门槛、回补选择、HTML/CSV 生成 |
| `newsflow/mail.py` | TLS SMTP、MIME、发送账本、pending 解决 |
| `newsflow/seal.py` | 临时正文 artifact 加密与验签 |
| `newsflow/state_io.py` | 元数据分支读写，仅提交固定文件 |
| `requirements-bpc.txt` | 实际安装核实的 BPC 运行依赖版本；非哈希锁文件 |
| `VERIFICATION.md` | 核实日期、实测结果与尚未验证的边界 |

## 8. 官方参考

- [GitHub Actions 额度与费用](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Schedule 的延迟、默认分支与停用条件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [GitHub Secrets 使用](https://docs.github.com/en/actions/how-tos/security-for-github-actions/security-guides/using-secrets-in-github-actions)
- [Gmail 应用专用密码及限制](https://support.google.com/accounts/answer/185833)
- [Gmail SMTP 配置](https://support.google.com/mail/answer/7104828)
- [Brevo 免费额度](https://help.brevo.com/hc/en-us/articles/208580669-FAQs-What-are-the-limits-of-the-Free-plan)
- [Brevo SMTP 凭证与配置](https://developers.brevo.com/docs/smtp-integration)
- [bpc-fetch 固定提交源码](https://github.com/Sophomoresty/bpc-fetch/tree/76e24f51f4bc7b1ed9bd91418796f3cf104be56b)

本项目适合“尽力收齐真实目录、清楚显示缺口、有限尝试提取、每天自己收邮件”。如果不可妥协的要求是“全站无遗漏 + 稳定付费全文 + 永久在线 + 永久零成本”，这些免费来源与平台不能提供这样的保证。
