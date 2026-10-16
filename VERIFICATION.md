# 核实与验证记录

核实日期：2026-10-16。使用本地 Windows Python 3.13 环境；交付的 GitHub workflow 目标为 Ubuntu 24.04 / Python 3.12。未替用户创建仓库或填写任何凭证，没有在用户 GitHub 账号实际运行或实际发信。

## 来源与 CLI

- 真实上游：[Sophomoresty/bpc-fetch](https://github.com/Sophomoresty/bpc-fetch)。已实际 clone 并读取 `pyproject.toml`、`cli.py`、`discover.py`、`sites.py`、`strategy.py`、`extract.py`。
- 固定提交：`76e24f51f4bc7b1ed9bd91418796f3cf104be56b`，声明包版本 `0.1.0`。
- 已创建工作目录中的独立 Python 环境，实际以 editable 方式安装该源码及依赖，实际执行 `fetch --help`、`discover --help` 和显式 sites.js 的 `doctor --compact`。
- `doctor` 返回 `ok=true`、936 个站点。其 Chromium 判断通过 `playwright install --dry-run` 实现，不能作为浏览器已成功启动的证明；随后已用本机 Edge 155.0.4283.45 成功启动全新浏览器并访问四个样本。工作流现在在安装 Chromium 后执行 `newsflow.browser_check` 实际启动检查；尚未在用户 GitHub runner 验证。
- `discover` 源码默认 20 条，RSS → sitemap → homepage → browser 依次回退，不合并所有来源；所以本项目没有拿它承担全量目录发现。
- sites.js 默认定位依赖仓库外层 `data/`。因此工作流保留完整源码 checkout，以 editable 安装并在实际 fetch 时传 `--sites-js`，不假定 PyPI wheel 带有这些数据。

## 当前环境的真实目录实测

使用交付配置、普通 User-Agent、robots 检查、7 天回溯、子地图预算每家 6 张。初次从空本地状态运行前三家，随后修复 robots 处理并单独复测 WSJ，得到以下结果（不是同一次并发快照）：

| 来源 | 新发现唯一链接 | 正常目录来源数 |
| --- | ---: | ---: |
| The Economist | 104 | 18 |
| NYT | 828 | 55 |
| FT | 715 | 16 |
| WSJ（修复后复测） | 293 | 7 |

**这不是出版总量、文章页验证成功数或覆盖率。** 数据是窗口内多来源发现的目录候选。FT 等可能有发布时间未知、仅近期修改的记录；目录中对此单独标注。首次运行前未有任何去重状态，后续每小时新增数通常不同。

WSJ 先前报告为 0，原因是本项目错误地把 RSS 域名 robots.txt 的 403 当成禁止抓取所有 RSS。实际 7 个官方 RSS 均返回 200。已按 [RFC 9309 §2.3.1.3](https://www.rfc-editor.org/rfc/rfc9309.html#section-2.3.1.3) 将普通 robots 4xx 视为规则不可用；429、5xx、网络失败仍延后，明确 Disallow 仍遵守。修复后 379 条窗口内来源记录合并为 293 条唯一链接。`www.wsj.com` 主站的明确 robots 限制仍保留，不能声称全覆盖。复测记录见 [access-fix.json](reports/access-fix.json)。

来源的详细实测记录见 [source-health.json](reports/source-health.json)。部分预设候选 RSS 返回失败、HTML 或不存在；失败会保留在报告，其他来源仍继续。未将失败候选的配置存在当作可用性验证。

开发中修正过两处实际发现的问题：FT RSS 的 syndication 查询参数造成同文重复，现在规范化时去掉所有查询参数；FT 历史地图里大量旧修改记录，现按 lastmod 做回溯筛选，但不把 lastmod 当发布时间。前三家数字来自去重/日期修正后的空状态采集；WSJ 数字来自本次 robots 修复后的独立复测。

## 403 与正文的修复后实测

初次原 CLI 三条样本均返回 403，旧结果保留于 [bpc-samples.json](reports/bpc-samples.json)。随后进行了四站普通 HTTP、全新 Edge 浏览器、curl_cffi 浏览器兼容请求及修复后的完整 Python 流程测试。

| 出版社 | 修复后的单篇结果 | 候选文本字符数 |
| --- | --- | ---: |
| The Economist | blocked_challenge，仍失败 | 0 |
| NYT | blocked_challenge，仍失败 | 0 |
| FT | partial_or_paid_unverified | 808 |
| WSJ | partial_or_paid_unverified | 1,347 |

上述 FT / WSJ 页面有付费标记，文本仅为部分或完整性未知的内容，**没有验证到付费全文**。实测普通请求收到 403/401；浏览器兼容传输改善了 FT/WSJ 的 HTTP 访问，但 HTTP 200 本身不证明得到文章。曾收到 FT 的 Subscribe to read 订阅套餐页，本次已增加排除；只将 article 元素交给上游提取器。

Economist 的 Cloudflare 验证、NYT 的 DataDome 验证在全新真实浏览器中也仍然阻挡。上游的策略优先级还会让部分站点不启动浏览器，因此仅安装 Chromium 或 doctor 返回 true 不等于解决。没有自动完成验证码，没有据此声称已消除访问限制。

最新样本 URL、分类、长度与哈希见 [access-fix.json](reports/access-fix.json)。未将真实文章正文放进交付包。样本只有每家一篇，不能推算整体成功率。用户要求纯 GitHub Actions；已增加 `access-check.yml` 无凭证诊断入口，实际 GitHub 出口仍待运行验证。

## 离线与配置验证

通过 19 项自动测试：

- URL 域名/路径验证、查询参数去重、移动与 AMP 规范化。
- RSS、Atom、sitemap 解析、实体声明拒绝、修改时间不冒充发布时间。
- 多来源合并、标题更新、子地图轮换。
- robots 403 后允许继续读取实际 RSS；429、503、明确 Disallow 仍阻止；200 验证/订阅页拒绝；付费片段标记与导航排除。
- 本地时区的日发送门槛、已发送/待核查去重、迟到记录与正文重试。
- HTML 转义、CSV 公式防护、MIME 附件构造、邮件头注入拒绝。
- SMTP DATA 前必须先有 pending 账本；账本提交失败不发送；发送中断后不自动重发。
- 临时内容加密/解密与密文篡改拒绝。
- 本地裸 Git 仓库状态往返，只提交三个允许的元数据文件。

另外：Python 源码编译检查通过；三份 YAML 解析通过；从 [rhysd/actionlint 官方发布页](https://github.com/rhysd/actionlint/releases) 下载的 **actionlint 1.7.12** 对三份 workflow 检查通过（未运行 shellcheck）。官方 Actions 的 v6/v4/v5 标签通过 `git ls-remote` 解析并写入具体提交 SHA。

初版曾生成 1,087 条目录、约 1.06 MB 的 MIME 预览。交付预览现已补入修复后的 WSJ 目录，正文不包含真实样本内容；未连接 SMTP。日报只选前一日及历史未发记录，因此不是整段 7 天目录总数。

未做的验证：用户 GitHub 的组织策略/权限、Ubuntu 上的全部依赖安装与浏览器启动、用户邮箱的 SMTP 登录/实际投递、所有目录链接逐页打开、全站目录总数、任何付费全文成功率。只有用户配置账号并运行后，才能验证这些环境相关环节。

## 费用与服务规则核实

- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)：公开仓库标准 runner 免费；Free 私有仓库包含 2,000 分钟/月。大型 runner 收费，artifact/cache 有容量限制。
- [Schedule 事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)：可能延迟/丢弃，仅默认分支执行；公开仓库无活动 60 天可能自动停用。
- [Brevo Free](https://help.brevo.com/hc/en-us/articles/208580669-FAQs-What-are-the-limits-of-the-Free-plan)：每天 300 封，不累计；[SMTP 文档](https://developers.brevo.com/docs/smtp-integration) 明确 SMTP key 与 API key 不同。
- [Gmail 应用密码](https://support.google.com/accounts/answer/185833)：要求两步验证，部分账号不可用，修改账户密码会撤销应用密码。

免费方案不是永久服务保证。具体账号审核、预算、服务规则或出版社访问条件改变后，应重新核查官方页面和健康报告。
