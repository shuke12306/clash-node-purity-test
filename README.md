# 节点纯净度检测 · clash-node-purity-test (CNPT)

[![CI](https://github.com/shuke12306/clash-node-purity-test/actions/workflows/ci.yml/badge.svg)](https://github.com/shuke12306/clash-node-purity-test/actions/workflows/ci.yml)

> 批量检测 Clash 节点出口 IP 纯净度，自动挑出各地区最干净的节点并生成报告。零配置自动发现 Clash 客户端，多源打分（IPPure + IPing），通用地区识别。
>
> *Batch-check the IP purity of Clash proxy nodes, pick the cleanest per region, and generate a report. Zero-config auto-discovery of Clash Verge.*

一个本地运行的命令行工具，批量检测 [Clash Verge](https://github.com/clash-verge-rev/clash-verge-rev) 里代理节点的**出口 IP 纯净度**，按地区挑出最干净的节点，并以**记事本报告 + 弹窗摘要**呈现结果。

> 适用场景：机场/自建节点的出口 IP 经常被标记为代理、机房或高风险，导致流媒体、AI 服务弹验证码或拒绝访问。逐个手查太麻烦，这个工具把「切节点 → 查 IP → 打分 → 排序 → 出报告」整个流程自动化了。
>
> 它**只读取**你本机的 Clash 配置和公开的 IP 查询服务，**不修改**你的任何 Clash 配置，也不连接任何第三方服务器。

## 特点

- **零配置**：自动从本机 Clash 客户端读出 API 地址、密钥、代理端口和当前 profile，免去手填。
- **通用地区识别**：参照 [ACL4SSR](https://github.com/ACL4SSR/ACL4SSR) 的分组规则，支持中文名、城市名、运营商别名、英文全称、两字母代码、国旗 emoji，兼容绝大多数机场命名。
- **多来源综合评分**：IPPure 欺诈分 + IPing 网页风险等级（50/50 加权），IPInfo 补充 ASN/地区。
- **IP 类型识别**：住宅 / 原生 / 机房 / 广播，按优先级选优。
- 自适应测试节奏 + 失败节点自动补测一轮。
- 支持只测部分地区，结果合并保存（不必整轮重跑）。

## 环境要求

- Python 3.9+
- 一个基于 mihomo（Clash.Meta）内核的客户端，并已开启「外部控制器」

### 支持的客户端与平台

自动探测会在常见配置目录里查找客户端的运行时 `config.yaml`（含 `external-controller` / `secret` / `mixed-port`），解析逻辑对 mihomo 内核类客户端通用：

| 客户端 | 自动探测 |
|--------|----------|
| [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev) | ✅ |
| Mihomo Party | ✅ |
| 其他 mihomo 内核客户端 | ⚠️ 取决于配置目录；探测不到时手填即可 |

| 平台 | 自动探测 | 报告打开 | 弹窗摘要 |
|------|----------|----------|----------|
| Windows | ✅ | 记事本/默认程序 | ✅ 原生弹窗 |
| macOS | ✅ | `open` 默认程序 | ⚠️ 降级为控制台摘要 |
| Linux | ✅ | `xdg-open` 默认程序 | ⚠️ 降级为控制台摘要 |

> 探测不到客户端（或用其他客户端）时，在 `local_config.json` 的 `clash.api` / `secret` / `http_proxy` 手填即可，对任何平台都通用。弹窗目前仅 Windows 有原生实现，其他平台报告内容照常生成、并在控制台打印摘要。

## 安装

```bash
git clone https://github.com/YOUR_NAME/clash-node-purity-test.git
cd clash-node-purity-test
pip install -r requirements.txt      # macOS / Linux 用 pip3
```

## 使用

### 一键启动（推荐）

- **Windows**：双击 `一键优选.bat`
- **macOS / Linux**：在项目目录执行 `./run.sh`（首次需 `chmod +x run.sh`，克隆时一般已带可执行位）

都会显示菜单：

1. 正常运行（纯净度测试 + 生成报告）
2. 仅测试节点（只写结果文件，不出报告）
3. 仅生成报告（用现有测试结果，不重新测试）

两个启动器都会透传额外参数，例如 `./run.sh --regions 台湾,日本 --report both`。

### 命令行

Windows 用 `python`，macOS / Linux 用 `python3`：

```bash
python node_purity_tool.py            # 测试后生成报告（默认）
python node_purity_tool.py test       # 只测试，写入结果文件
python node_purity_tool.py report     # 只用现有结果生成报告
python node_purity_tool.py menu       # 显示 1/2/3 菜单
```

常用参数：

| 参数 | 说明 |
|------|------|
| `--regions 台湾,日本` | 只测指定地区（仅 `menu`/`all`/`test` 有效）；结果合并进现有文件，其他地区保留 |
| `--report notepad\|popup\|both\|none` | 报告呈现方式：打开报告文件 / 弹窗 / 两者 / 都不（默认两者） |
| `--format text\|html\|both` | 报告格式：纯文本 / 自包含 HTML / 两者都出（默认 text） |
| `--no-detect` | 跳过 Clash Verge 自动探测，只用 `local_config.json` / 默认值 |
| `--yes` / `-y` | 无人值守，跳过询问 |
| `--verbose` | 显示详细请求与重试日志 |
| `--log-file <路径>` | 把详细日志写入文件 |

> **平台差异**：`--report notepad` 在 Windows 用记事本打开报告，macOS / Linux 用系统默认程序打开（`open` / `xdg-open`）。`--report popup` 的原生弹窗仅 Windows 有；macOS / Linux 会自动跳过（各地区最优摘要已打印在控制台）。参数名保持一致，效果按平台自适应。

## 配置（通常不需要）

得益于自动探测，**多数情况下无需任何配置即可运行**。需要覆盖默认行为时，复制示例文件再改：

```bash
copy local_config.json.example local_config.json
```

`local_config.json` 里所有字段都可留空，留空即用自动探测值。常用可覆盖项：

| 字段 | 说明 |
|------|------|
| `clash.api` / `secret` / `http_proxy` | 留空时自动从本机 Clash Verge 读取 |
| `clash.select_group` | 切换节点用的 Selector 组名；留空时自动选取 |
| `paths.config_file` | 自定义节点源文件；**留空时自动用 Clash Verge 当前 profile** |
| `regions.target` | 每次默认测试的地区 |
| `report.open_notepad` / `popup` | 报告是否开记事本 / 弹窗 |
| `report.format` | 报告格式 `text` / `html` / `both`（默认 `text`） |
| `report.history_keep` | 每节点在历史归档里保留的最近记录数（趋势用，默认 50） |
| `purity_sources.ipinfo_token` | 已内置一个可分发 token；想用自己的再填 |
| `region_match` | 地区识别别名表，默认已覆盖主流命名；需要新增地区时才填 |

### 自定义节点源（可选）

默认直接用 Clash Verge 当前 profile 的 `proxies` 作为节点源。若想用单独的节点文件，复制 `config.example.yaml` 为 `config.yaml`，填入节点，再在 `local_config.json` 把 `paths.config_file` 指向它。

### 扩展地区识别（可选）

`region_match` 是地区别名表，格式：

```json
"region_match": {
  "巴西": { "code": "BR", "names": ["巴西", "圣保罗"], "en": ["Brazil"] }
}
```

- `code`：两字母国家代码，用于生成国旗 emoji 并做带词边界的代码匹配
- `names`：中文名、城市名、运营商别名等
- `en`：英文全称/城市名（大小写不敏感）

## 工作原理

1. 自动发现本机 Clash Verge 的连接参数和当前 profile。
2. 读取 profile（或自定义 `config.yaml`）的 `proxies`，按 `--regions` 筛选。
3. 通过 Clash 外部控制器逐个切换节点，回读确认切换生效（测完恢复原选中节点，不影响你的出口）。
4. 走代理访问 IPPure 取出口 IP，再用 IPInfo / IPing 补充 ASN、地区、代理属性、风险信息。
5. 综合纯净度分 = IPPure `fraudScore` 与 IPing 网页风险百分比 50/50 加权；IPInfo 仅作参考。
6. 节点间隔自适应，疑似限流时放缓；测完对无分节点自动补测一轮。
7. 排序写入结果文件，生成报告：各地区最优、各区 TOP5、高风险节点（仅复核）。

### 报告文件命名

每次生成的报告**不覆盖旧文件**，文件名带配置名和时间戳，便于对比不同时间/不同订阅的结果：

```text
节点纯净度报告_<配置名>_<日期时间>.txt
例：节点纯净度报告_2029_20260629_043630.txt
```

- **配置名**取 Clash Verge 当前 profile 的显示名；取不到时回退到节点源文件名，仍取不到则省略。
- 基础名（`节点纯净度报告`）可在 `local_config.json` 的 `paths.report_file` 自定义。
- 报告文件是本机产物，已在 `.gitignore` 中，不会被提交。旧报告需要清理时自行删除即可。

### 历史趋势与 HTML 报告

- **历史趋势**：每轮测试会把各节点的综合分追加进 `node_history.jsonl`（只追加，本机产物，已忽略提交）。报告里会多出一段「趋势」，显示每个节点最近几次的分数走势（`最新 ← 次新 ← …`）和方向标记（↓改善 / ↑恶化 / →平稳）。首次运行因无历史会自动跳过该段。
- **保留条数**：在 `local_config.json` 的 `report.history_keep` 控制每节点保留的最近记录数（默认 50），超量时自动压实归档文件。
- **HTML 报告**：`--format html`（或 `both`）生成一份自包含的 HTML 报告，内联样式、无任何外部依赖，可直接用浏览器打开或分享。节点名等内容均做了 HTML 转义，恶意命名无法破坏页面。也可在 `report.format` 里设默认格式。

### 数据来源与打分逻辑

纯净度数据来自三个公开服务，但**只有两个参与综合打分**：

| 来源 | 取什么 | 是否参与综合分 |
|------|--------|----------------|
| **IPPure**（`my.ippure.com`） | 出口 IP、欺诈分 `fraudScore`、住宅/广播标记 | ✅ 参与，权重 **50%** |
| **IPing 网页版**（`iping.cc/ip/{ip}`） | 网页「风险等级」百分比 | ✅ 参与，权重 **50%** |
| **IPInfo**（`ipinfo.io`） | ASN、地区归属 | ❌ 不参与，仅作参考 |

- **综合纯净度分 = IPPure `fraudScore` × 50% + IPing 网页风险百分比 × 50%**（默认权重），分越低越干净。
- **权重可配**：在 `local_config.json` 的 `purity_sources.weights` 里改各源权重，例如 `{"ippure": 0.7, "iping_web": 0.3}`。留空或非法值（负数/非数字/全零）会自动回退到等权平均。
- **自动降级**：某个源临时挂了（限流、超时、返回异常），它会退出本次打分，**剩余源的权重自动重新归一化**——不会因为少一个源就把分数算高或算低。所有源都挂了才标记为「无评分」。
- **数据源健康度**：每轮测试结束后，控制台和报告都会打印一行各源成功率，例如 `IPPure 28/30 · IPing网页 30/30 · IPInfo 0/30（疑似不可用）`，让你一眼看出某个源是不是整体不可用，而不是误以为节点有问题。
- IPing 有两个接口：**网页版**的「风险等级」百分比参与打分；它的 **API**（`api.iping.cc`）返回的 `risk_score` 含义不稳定，只用于 IP 类型 / 代理 / 风险标签参考，**不参与数值评分**。
- IPInfo 使用项目内置的可分发 token，只补充 ASN 和地区，**不影响分数**。
- IP 类型（住宅 / 原生 / 机房 / 广播）综合 IPPure 的住宅/广播标记与 IPing 的类型标签得出，用于选优**排序**，不是分数本身。

### 评分分级（综合分越低越干净）

| 综合分 | 分级 | 是否参选 |
|--------|------|----------|
| 0–25 | 低风险 ✅ | 是 |
| 26–50 | 中风险 🟡 | 是 |
| 51–75 | 高风险 ⚠️ | 否（仅复核） |
| 76–100 | 极高风险 ‼️ | 否（仅复核） |

选优时先剔除高/极高风险，再按 IP 类型优先级（住宅 > 原生 > 普通 > 机房 > 广播），最后按综合分排序。

## 安全与隐私

- `local_config.json`、`config.yaml`、结果文件、报告文件都是你的本机私有数据，已在 `.gitignore` 中，不会被提交。
- 本工具只读取本机 Clash 配置、向公开 IP 查询服务发起请求，**不修改你的 Clash 配置，不上传任何节点信息到第三方**。

## 数据来源致谢

- [IPPure](https://my.ippure.com) — 出口 IP 与欺诈分
- [IPInfo](https://ipinfo.io) — ASN / 地区
- [IPing](https://www.iping.cc) — IP 类型与网页风险等级
- [ACL4SSR](https://github.com/ACL4SSR/ACL4SSR) — 地区识别正则参考

## 免责声明

本工具仅供学习和个人网络配置优化使用。使用者需自行遵守当地法律法规及相关服务的使用条款。作者不对使用本工具造成的任何后果负责。

## License

[MIT](./LICENSE)

---

<sub>关键词 / Topics: `clash` · `clash-verge` · `mihomo` · `proxy` · `proxy-checker` · `ip-quality` · `ip-purity` · `fraud-score` · `python` · `windows`</sub>
