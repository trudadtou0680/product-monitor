# Theme Fund Analyzer Skill

`theme-fund-analyzer` 是一个 Codex skill，用于国内公募基金零售、营销、产品团队围绕主题基金产品池抓取公开数据，计算区间收益率、名单内排名、合并份额规模、最大回撤，并输出可追溯的数据缺口和异常清单。

本仓库面向公开查看和命令行安装。安装完成后需要重启 Codex，新的 skill 才会被识别。

## 目录结构

```text
theme-fund-analyzer/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── analysis-rules.md
│   ├── data-sources.md
│   └── product-pools.md
└── scripts/
    └── fetch_theme_funds.py
```

## 安装

使用 Codex 官方 skill-installer 从 GitHub 安装：

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo trudadtou0680/product-monitor \
  --path theme-fund-analyzer
```

也可以使用本仓库的一键安装脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/trudadtou0680/product-monitor/main/install.sh | bash
```

默认安装到：

```text
${CODEX_HOME:-$HOME/.codex}/skills/theme-fund-analyzer
```

指定分支、标签或安装目录：

```bash
curl -fsSL https://raw.githubusercontent.com/trudadtou0680/product-monitor/main/install.sh | \
  bash -s -- --ref main --dest "$HOME/.codex/skills"
```

## 更新

```bash
curl -fsSL https://raw.githubusercontent.com/trudadtou0680/product-monitor/main/update.sh | bash
```

`install.sh` 和 `update.sh` 在覆盖已有 skill 前，会先备份旧目录到同级目录：

```text
theme-fund-analyzer.backup-YYYYMMDDHHMMSS
```

## 使用示例

安装并重启 Codex 后，可以直接提出类似需求：

```text
Use $theme-fund-analyzer to rank CPO funds by recent 1-month return and show size, drawdown, and data gaps.
```

脚本也可以独立运行：

```bash
python3 theme-fund-analyzer/scripts/fetch_theme_funds.py \
  --theme CPO \
  --period 1m \
  --sort return \
  --top 30
```

## 验证

校验 skill 结构：

```bash
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py theme-fund-analyzer
```

校验脚本入口：

```bash
python3 theme-fund-analyzer/scripts/fetch_theme_funds.py --help
```

校验安装脚本语法：

```bash
bash -n install.sh
bash -n update.sh
```

## 依赖与数据来源

- 运行环境：Python 3，Bash，curl，tar。
- 数据来源：天天基金/东方财富公开接口、东方财富概念板块接口，以及 skill 内 `references/` 记录的字段口径和分析规则。
- 产品池事实源：`theme-fund-analyzer/references/product-pools.md`。

## 风险提示

本 skill 只处理公开基金数据分析，不承诺收益，不替代投顾建议。公开接口失效、字段缺失或基金名称代码不一致时，应输出数据缺口或待确认项，不得编造收益、回撤、规模或排名。
