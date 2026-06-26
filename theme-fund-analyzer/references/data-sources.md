# 数据源与字段口径

## 首选数据源

- 天天基金/东方财富公开接口用于基金搜索、净值序列、规模变动。
- 东方财富概念板块接口用于全新主题的细分概念板块发现，覆盖 CPO、PCB、先进封装、存储芯片等比天天基金主题更细的概念。
- AKShare 公募基金文档用于确认东方财富/天天基金接口覆盖范围和字段释义，尤其是开放式基金排行、场内基金排行、基金规模、规模份额等接口说明。

## 接口用途

- 基金名称反查：`https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx?m=1&key={基金名称或代码}`。
- 天天基金主题/板块列表：`https://fundmobapi.eastmoney.com/FundMNewApi/FundMNSubjectList`，仅作为粗主题参考，不作为全新细分主题的主来源。
- 天天基金主题基金列表：`https://fundmobapi.eastmoney.com/FundMNewApi/FundMNRank`，使用 `TOPICAL={主题代码}` 过滤，仅作为粗主题参考。
- 东方财富概念板块列表：`https://push2delay.eastmoney.com/api/qt/clist/get?fs=m:90+t:3&fields=f12,f14,f3,f20,f62,f128,f136,f152`；备用域名为 `push2his.eastmoney.com`、`push2.eastmoney.com`。
- 东方财富概念板块成分股：`https://push2delay.eastmoney.com/api/qt/clist/get?fs=b:{概念板块代码}&fields=f12,f14,f3,f2,f20,f62`；备用域名同上。
- 东方财富板块主题基金：`https://quote.eastmoney.com/newapi/bk/jj/{概念板块名称}`，用于获取概念板块页面侧栏展示的主题基金。
- 阶段涨幅：`https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jdzf&code={基金代码}`。
- 净值序列：`https://fund.eastmoney.com/pingzhongdata/{基金代码}.js?v={YYYYMMDD}`。
- 规模变动：`https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd&code={基金代码}`。

## 字段口径

- 基金代码：6 位公募基金代码。
- 基金名称：公开接口返回的基金简称为输出名称；`references/product-pools.md` 中的基金名称用于展示校验和代码缺失时的降级匹配。
- 区间收益率：最新一日、近 1 周、近 1 月、近 3 月、近 6 月、近 1 年、今年来优先使用天天基金阶段涨幅字段；公开阶段涨幅缺失时再使用累计净值序列兜底计算，公式为 `区间结束累计净值 / 区间起始累计净值 - 1`。
- 最大回撤：在有效区间内基于累计净值序列计算，公式为 `当前累计净值 / 区间内历史峰值 - 1` 的最小值。
- 合并份额规模：同一产品不同份额最近报告期“期末净资产（亿元）”求和。
- 规模截止日：参与合并份额中最新的规模报告期；若份额规模报告期不一致，输出异常标记。
- 全新主题池：优先由东方财富概念板块确定标准主题名称和概念板块代码；若东方财富板块主题基金接口返回基金列表，直接使用该列表生成临时基金池；若主题基金接口无返回，再基于概念板块名称和用户主题关键词在天天基金全量基金目录中匹配生成，并必须标注来源。

## 降级规则

- HTTPS 请求默认使用 `--tls-mode auto`：先校验证书，若遇到公司代理/自签证书链导致校验失败，则自动降级继续抓取并输出风险提示；安全敏感环境可用 `--tls-mode verify` 强制不降级，受控调试可用 `--tls-mode insecure` 或 `--insecure` 跳过校验。
- 默认 User-Agent 使用透明工具标识 `theme-fund-analyzer/1.0`，不默认模拟浏览器；确需适配公开接口时可通过 `--user-agent` 或 `--referer` 显式覆盖。
- 阶段涨幅接口缺失时，可降级使用累计净值序列计算，并必须标记 `天天基金阶段涨幅缺失，使用净值序列计算`。
- 净值接口缺失累计净值时，可降级使用单位净值序列，但必须标记 `累计净值缺失`。
- 规模接口缺失时，规模字段置空并标记 `规模缺失`。
- 搜索接口无法唯一匹配时，该产品进入“待确认产品”，不得进入数值排名。
- 接口超时或格式变化时，输出接口异常，不得编造收益、回撤、规模或排名。
