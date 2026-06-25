#!/usr/bin/env python3
"""Fetch and rank theme fund products from public Eastmoney/Tiantian Fund data."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_POOL_REFERENCE = BASE_DIR / "references" / "product-pools.md"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MOBILE_PARAMS = {
    "appType": "ttjj",
    "product": "EFund",
    "plat": "Android",
    "deviceid": "theme-fund-analyzer",
    "Version": "6.5.5",
}
EASTMONEY_CLIST_HOSTS = [
    "push2delay.eastmoney.com",
    "push2his.eastmoney.com",
    "push2.eastmoney.com",
]
SHARE_SUFFIX_RE = re.compile(r"[\s\-_]*(A|B|C|D|E|F|H|I|Y|R|O|人民币|美元现汇|美元现钞)$", re.I)
CLASS_TOKENS = {"A", "B", "C", "D", "E", "F", "H", "I", "Y", "R", "O"}


@dataclass
class PoolItem:
    theme: str
    input_name: str
    input_code: str
    row: int


@dataclass
class Candidate:
    code: str
    name: str
    fund_type: str = ""
    company: str = ""
    manager: str = ""

    @property
    def share_class(self) -> str:
        return detect_share_class(self.name)

    @property
    def base_key(self) -> str:
        return base_key(self.name)


@dataclass
class ConceptBoard:
    code: str
    name: str
    change_pct: float | None = None
    market_value: float | None = None


@dataclass
class BoardStock:
    code: str
    name: str


@dataclass
class NavPoint:
    date: dt.date
    value: float


@dataclass
class ScalePoint:
    code: str
    name: str
    date: dt.date | None
    value: float | None


@dataclass
class ProductResult:
    theme: str
    input_name: str
    input_code: str
    fund_code: str = ""
    fund_name: str = ""
    analysis_code: str = ""
    analysis_name: str = ""
    rank: int | None = None
    return_rank: int | None = None
    drawdown_rank: int | None = None
    scale_rank: int | None = None
    display_returns: dict[str, float | None] = field(default_factory=dict)
    display_return_ranks: dict[str, int | None] = field(default_factory=dict)
    display_return_dates: dict[str, str] = field(default_factory=dict)
    return_pct: float | None = None
    max_drawdown_pct: float | None = None
    merged_scale: float | None = None
    scale_date: dt.date | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    source: str = "天天基金/东方财富公开接口"
    issues: list[str] = field(default_factory=list)
    conflict: str = ""

    def sort_value(self, key: str) -> float | None:
        if key == "return":
            return self.return_pct
        if key == "drawdown":
            return self.max_drawdown_pct
        if key == "scale":
            return self.merged_scale
        raise ValueError(f"Unsupported sort key: {key}")


class Fetcher:
    def __init__(self, timeout: float = 8.0, retries: int = 2, sleep: float = 0.15):
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep
        self.ctx = ssl._create_unverified_context()
        self.search_cache: dict[str, list[Candidate]] = {}
        self.catalog_cache: list[Candidate] | None = None
        self.catalog_key_cache: list[tuple[str, Candidate]] | None = None
        self.catalog_code_cache: dict[str, Candidate] | None = None
        self.nav_cache: dict[str, tuple[list[NavPoint], str]] = {}
        self.scale_cache: dict[str, ScalePoint] = {}
        self.stage_return_cache: dict[str, dict[str, float]] = {}
        self.subject_cache: list[dict[str, Any]] | None = None
        self.concept_board_cache: list[ConceptBoard] | None = None
        self.board_stock_cache: dict[str, list[BoardStock]] = {}
        self.board_theme_fund_cache: dict[str, list[PoolItem]] = {}

    def get_text(self, url: str, *, encoding: str = "utf-8") -> str:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://fund.eastmoney.com/"})
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as resp:
                    raw = resp.read()
                if self.sleep:
                    time.sleep(self.sleep)
                return raw.decode(encoding, errors="ignore")
            except Exception as exc:  # noqa: BLE001 - external endpoint failures are surfaced as data gaps
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.4 * (attempt + 1))
        raise RuntimeError(str(last_error))

    def get_json(self, url: str) -> dict[str, Any]:
        return json.loads(self.get_text(url, encoding="utf-8-sig"))

    def get_clist_json(self, params: dict[str, str]) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        errors: list[str] = []
        for host in EASTMONEY_CLIST_HOSTS:
            url = f"https://{host}/api/qt/clist/get?{query}"
            try:
                return self.get_json(url)
            except Exception as exc:  # noqa: BLE001 - try the next Eastmoney quote host
                errors.append(f"{host}:{exc}")
        raise RuntimeError("东方财富行情接口异常：" + "；".join(errors))

    def search(self, keyword: str) -> list[Candidate]:
        key = normalize_space(keyword)
        if key in self.search_cache:
            return self.search_cache[key]
        catalog_candidates = self.search_catalog(key)
        if catalog_candidates:
            self.search_cache[key] = catalog_candidates
            return catalog_candidates
        url = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx?m=1&key=" + urllib.parse.quote(key)
        candidates: list[Candidate] = []
        try:
            text = self.get_text(url)
            data = json.loads(text)
            for item in data.get("Datas") or []:
                if item.get("CATEGORYDESC") != "基金" and not item.get("FundBaseInfo"):
                    continue
                code = str(item.get("CODE") or item.get("_id") or "").strip()
                name = str(item.get("NAME") or "").strip()
                if not re.fullmatch(r"\d{6}", code) or not name:
                    continue
                base = item.get("FundBaseInfo") or {}
                candidates.append(
                    Candidate(
                        code=code,
                        name=name,
                        fund_type=str(base.get("FTYPE") or ""),
                        company=str(base.get("JJGS") or ""),
                        manager=str(base.get("JJJL") or ""),
                    )
                )
        except Exception:
            candidates = []
        merged = {candidate.code: candidate for candidate in candidates}
        for candidate in catalog_candidates:
            merged.setdefault(candidate.code, candidate)
        self.search_cache[key] = list(merged.values())
        return self.search_cache[key]

    def catalog(self) -> list[Candidate]:
        if self.catalog_cache is not None:
            return self.catalog_cache
        url = "https://fund.eastmoney.com/js/fundcode_search.js"
        text = self.get_text(url, encoding="utf-8-sig")
        match = re.search(r"var\s+r\s*=\s*(.*?);", text, re.S)
        if not match:
            raise RuntimeError("基金目录格式不可解析")
        data = json.loads(match.group(1))
        catalog: list[Candidate] = []
        for row in data:
            if not isinstance(row, list) or len(row) < 4:
                continue
            code = str(row[0]).strip()
            name = str(row[2]).strip()
            if re.fullmatch(r"\d{6}", code) and name:
                catalog.append(Candidate(code=code, name=name, fund_type=str(row[3] or "")))
        self.catalog_cache = catalog
        return catalog

    def search_catalog(self, keyword: str) -> list[Candidate]:
        target = comparable_key(keyword)
        if not target:
            return []
        if self.catalog_key_cache is None:
            self.catalog_key_cache = [(comparable_key(candidate.name), candidate) for candidate in self.catalog()]
        output: list[Candidate] = []
        for current, candidate in self.catalog_key_cache:
            if target == current or target in current or current in target:
                output.append(candidate)
        return output[:50]

    def get_by_code(self, code: str) -> Candidate | None:
        code = code_string(code)
        if not re.fullmatch(r"\d{6}", code):
            return None
        if self.catalog_code_cache is None:
            self.catalog_code_cache = {candidate.code: candidate for candidate in self.catalog()}
        candidate = self.catalog_code_cache.get(code)
        if candidate:
            return candidate
        try:
            for item in self.search(code):
                if item.code == code:
                    return item
        except Exception:
            return None
        return None

    def fetch_nav(self, code: str) -> tuple[list[NavPoint], str]:
        if code in self.nav_cache:
            return self.nav_cache[code]
        stamp = dt.date.today().strftime("%Y%m%d")
        url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js?v={stamp}"
        text = self.get_text(url, encoding="utf-8-sig")
        source_flag = "累计净值"
        points = parse_acworth_trend(text)
        if not points:
            source_flag = "单位净值"
            points = parse_networth_trend(text)
        if not points:
            raise RuntimeError("净值序列为空")
        self.nav_cache[code] = (points, source_flag)
        return points, source_flag

    def fetch_scale(self, candidate: Candidate) -> ScalePoint:
        if candidate.code in self.scale_cache:
            return self.scale_cache[candidate.code]
        url = f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd&code={candidate.code}"
        text = self.get_text(url, encoding="utf-8-sig")
        point = parse_scale_table(candidate, text)
        self.scale_cache[candidate.code] = point
        return point

    def fetch_stage_returns(self, code: str) -> dict[str, float]:
        if code in self.stage_return_cache:
            return self.stage_return_cache[code]
        returns: dict[str, float] = {}
        try:
            points, _ = self.fetch_nav(code)
            latest = points[-1]
            prev = points[-2] if len(points) >= 2 else None
            if prev and prev.value:
                returns["latest-day"] = (latest.value / prev.value - 1) * 100
        except Exception:
            pass
        try:
            url = f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jdzf&code={code}"
            text = self.get_text(url, encoding="utf-8-sig")
            returns.update(parse_stage_return_table(text))
        except Exception:
            pass
        self.stage_return_cache[code] = returns
        return returns

    def fetch_subjects(self) -> list[dict[str, Any]]:
        if self.subject_cache is not None:
            return self.subject_cache
        url = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNSubjectList?" + urllib.parse.urlencode(MOBILE_PARAMS)
        data = self.get_json(url)
        self.subject_cache = data.get("Datas") or []
        return self.subject_cache

    def fetch_topical_pool(self, subject_code: str, theme: str, page_size: int = 100) -> list[PoolItem]:
        items: list[PoolItem] = []
        page = 1
        while True:
            params = {
                **MOBILE_PARAMS,
                "FundType": "0",
                "SortColumn": "SYL_Z",
                "Sort": "desc",
                "pageIndex": str(page),
                "pageSize": str(page_size),
                "TOPICAL": subject_code,
                "DataConstraintType": "0",
            }
            url = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNRank?" + urllib.parse.urlencode(params)
            data = self.get_json(url)
            rows = data.get("Datas") or []
            if not rows:
                break
            for idx, row in enumerate(rows, start=len(items) + 1):
                code = code_string(row.get("FCODE"))
                name = normalize_space(row.get("SHORTNAME"))
                if code and name:
                    items.append(PoolItem(theme=theme, input_name=name, input_code=code, row=idx))
            total = int(data.get("TotalCount") or 0)
            if total and len(items) >= total:
                break
            if len(rows) < page_size:
                break
            page += 1
            if page > 20:
                break
        return items

    def fetch_concept_boards(self, page_size: int = 100) -> list[ConceptBoard]:
        if self.concept_board_cache is not None:
            return self.concept_board_cache
        boards: list[ConceptBoard] = []
        page = 1
        total = 0
        while True:
            params = {
                "pn": str(page),
                "pz": str(page_size),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f3",
                "fs": "m:90+t:3",
                "fields": "f12,f14,f3,f20,f62,f128,f136,f152",
            }
            data = self.get_clist_json(params).get("data") or {}
            rows = data.get("diff") or []
            total = int(data.get("total") or total or 0)
            for row in rows:
                code = normalize_space(row.get("f12"))
                name = normalize_space(row.get("f14"))
                if code.startswith("BK") and name:
                    boards.append(
                        ConceptBoard(
                            code=code,
                            name=name,
                            change_pct=parse_float(str(row.get("f3"))),
                            market_value=parse_float(str(row.get("f20"))),
                        )
                    )
            if not rows or len(rows) < page_size or (total and len(boards) >= total):
                break
            page += 1
            if page > 20:
                break
        self.concept_board_cache = boards
        return boards

    def fetch_concept_constituents(self, board_code: str, page_size: int = 100) -> list[BoardStock]:
        if board_code in self.board_stock_cache:
            return self.board_stock_cache[board_code]
        stocks: list[BoardStock] = []
        page = 1
        total = 0
        while True:
            params = {
                "pn": str(page),
                "pz": str(page_size),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f3",
                "fs": f"b:{board_code}",
                "fields": "f12,f14,f3,f2,f20,f62",
            }
            data = self.get_clist_json(params).get("data") or {}
            rows = data.get("diff") or []
            total = int(data.get("total") or total or 0)
            for row in rows:
                code = normalize_space(row.get("f12"))
                name = normalize_space(row.get("f14"))
                if code and name:
                    stocks.append(BoardStock(code=code, name=name))
            if not rows or len(rows) < page_size or (total and len(stocks) >= total):
                break
            page += 1
            if page > 20:
                break
        self.board_stock_cache[board_code] = stocks
        return stocks

    def fetch_board_theme_funds(self, board_name: str, theme: str) -> list[PoolItem]:
        key = normalize_space(board_name)
        if key in self.board_theme_fund_cache:
            return self.board_theme_fund_cache[key]
        url = "https://quote.eastmoney.com/newapi/bk/jj/" + urllib.parse.quote(key)
        rows = json.loads(self.get_text(url, encoding="utf-8-sig"))
        items: list[PoolItem] = []
        if isinstance(rows, list):
            for idx, row in enumerate(rows, start=1):
                if not isinstance(row, dict):
                    continue
                code = code_string(row.get("stockCode"))
                name = normalize_space(row.get("fundName"))
                if code and name:
                    items.append(PoolItem(theme=theme, input_name=name, input_code=code, row=idx))
        self.board_theme_fund_cache[key] = items
        return items


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_name(value: str) -> str:
    value = normalize_space(value).upper()
    value = value.replace("（", "(").replace("）", ")")
    value = re.sub(r"[\s_\-·()（）]", "", value)
    value = value.replace("發起式", "发起式")
    return value


def base_key(value: str) -> str:
    text = normalize_name(value)
    text = text.replace("发起式", "").replace("发起", "").replace("灵活配置", "")
    text = SHARE_SUFFIX_RE.sub("", text)
    return text


def comparable_key(value: str) -> str:
    return base_key(value)


def board_key(value: str) -> str:
    text = normalize_name(value)
    for token in ["板块", "主题", "概念", "基金", "行业", "产业", "设备", "材料"]:
        text = text.replace(token, "")
    return text


def keyword_tokens(value: str) -> list[str]:
    text = board_key(value)
    tokens: list[str] = []
    if len(text) >= 2:
        tokens.append(text)
    tokens.extend(re.findall(r"[A-Z0-9]{2,}", normalize_name(value)))
    if "半导体" in comparable_key(value):
        tokens.append("半导体")
    if "集成电路" in comparable_key(value):
        tokens.append("集成电路")
    if "人工智能" in comparable_key(value):
        tokens.append("人工智能")
    if "通信" in comparable_key(value):
        tokens.append("通信")
    if "CPO" in normalize_name(value):
        tokens.append("CPO")
    if "PCB" in normalize_name(value):
        tokens.append("PCB")
    return dedupe([token for token in tokens if token])


def detect_share_class(value: str) -> str:
    text = normalize_space(value)
    match = SHARE_SUFFIX_RE.search(text)
    if match:
        token = match.group(1).upper()
        return token if token in CLASS_TOKENS else token
    return "A"


def explicit_share_class(value: str) -> str | None:
    text = normalize_space(value)
    match = SHARE_SUFFIX_RE.search(text)
    if not match:
        return None
    token = match.group(1).upper()
    return token if token in CLASS_TOKENS else token


def code_string(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    if text.isdigit():
        return text.zfill(6)
    return text


def load_pool_reference(path: Path) -> dict[str, list[PoolItem]]:
    pools: dict[str, list[PoolItem]] = {}
    current_theme = ""
    header: list[str] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if line.startswith("## "):
            current_theme = normalize_space(line[3:])
            pools.setdefault(current_theme, [])
            header = []
            continue
        if not current_theme or not line.startswith("|"):
            continue
        cells = [normalize_space(cell) for cell in line.strip("|").split("|")]
        if not cells or all(re.fullmatch(r"-+", cell.replace(":", "").strip()) for cell in cells):
            continue
        if any("基金代码" in cell for cell in cells) and any("基金名称" in cell for cell in cells):
            header = cells
            continue
        if len(cells) < 2:
            continue
        if header:
            code_idx = next((idx for idx, cell in enumerate(header) if "基金代码" in cell), 0)
            name_idx = next((idx for idx, cell in enumerate(header) if "基金名称" in cell), 1)
        else:
            code_idx, name_idx = 0, 1
        code = code_string(cells[code_idx] if code_idx < len(cells) else "")
        name = normalize_space(cells[name_idx] if name_idx < len(cells) else "")
        if code or name:
            pools[current_theme].append(PoolItem(theme=current_theme, input_name=name, input_code=code, row=line_no))
    return {theme: items for theme, items in pools.items() if items}


def dedupe_pool_items(items: list[PoolItem]) -> list[PoolItem]:
    groups: dict[str, list[PoolItem]] = {}
    order: list[str] = []
    for item in items:
        key = base_key(item.input_name) if item.input_name else code_string(item.input_code)
        if not key:
            key = f"row:{item.row}"
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(item)
    return [choose_display_item(groups[key]) for key in order]


def choose_display_item(items: list[PoolItem]) -> PoolItem:
    def priority(item: PoolItem) -> tuple[int, int, int]:
        share = explicit_share_class(item.input_name) or detect_share_class(item.input_name)
        share_priority = 0 if share == "A" else 1
        code_priority = 0 if re.fullmatch(r"\d{6}", item.input_code or "") else 1
        return share_priority, code_priority, item.row

    return sorted(items, key=priority)[0]


def match_subject(fetcher: Fetcher, theme: str) -> dict[str, Any] | None:
    target = board_key(theme)
    candidates: list[tuple[int, dict[str, Any]]] = []
    for subject in fetcher.fetch_subjects():
        name = normalize_space(subject.get("INDEXNAME"))
        current = board_key(name)
        if not current:
            continue
        score = 0
        if current == target:
            score = 100
        elif target and (target in current or current in target):
            score = 80
        elif any(token in current for token in keyword_tokens(theme)):
            score = 50
        if score:
            candidates.append((score, subject))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    top_score = candidates[0][0]
    top = [subject for score, subject in candidates if score == top_score]
    return top[0] if len(top) == 1 or top_score >= 80 else None


def discover_keyword_pool(fetcher: Fetcher, theme: str) -> list[PoolItem]:
    tokens = keyword_tokens(theme)
    if not tokens:
        return []
    items: list[PoolItem] = []
    for candidate in fetcher.catalog():
        name_key = comparable_key(candidate.name)
        if any(token in name_key for token in tokens):
            items.append(PoolItem(theme=theme, input_name=candidate.name, input_code=candidate.code, row=len(items) + 1))
    return items


def match_concept_board(fetcher: Fetcher, theme: str) -> ConceptBoard | None:
    target = board_key(theme)
    tokens = keyword_tokens(theme)
    candidates: list[tuple[int, ConceptBoard]] = []
    for board in fetcher.fetch_concept_boards():
        current = board_key(board.name)
        if not current:
            continue
        score = 0
        if current == target:
            score = 100
        elif target and (target in current or current in target):
            score = 85
        elif any(token and token in current for token in tokens):
            score = 65
        if score:
            candidates.append((score, board))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], len(board_key(item[1].name))), reverse=True)
    top_score = candidates[0][0]
    top = [board for score, board in candidates if score == top_score]
    if len(top) == 1 or top_score >= 85:
        return top[0]
    return None


def discover_concept_pool(fetcher: Fetcher, theme: str) -> tuple[list[PoolItem], str]:
    board = match_concept_board(fetcher, theme)
    if not board:
        return [], "未在东方财富概念板块中发现唯一匹配主题"
    try:
        constituents = fetcher.fetch_concept_constituents(board.code)
    except Exception:
        constituents = []
    try:
        theme_funds = fetcher.fetch_board_theme_funds(board.name, theme)
    except Exception:
        theme_funds = []
    board_source = f"东方财富概念板块：{board.name}({board.code}"
    if constituents:
        board_source += f"，成分股{len(constituents)}只"
    board_source += ")"
    if theme_funds:
        return theme_funds, f"{board_source}；东方财富板块主题基金接口"
    tokens = dedupe(keyword_tokens(theme) + keyword_tokens(board.name))
    items: list[PoolItem] = []
    for candidate in fetcher.catalog():
        name_key = comparable_key(candidate.name)
        if any(token in name_key for token in tokens):
            items.append(PoolItem(theme=theme, input_name=candidate.name, input_code=candidate.code, row=len(items) + 1))
    if items:
        source = f"{board_source}；东方财富板块主题基金接口无返回；基金目录关键词匹配：{', '.join(tokens)}"
    else:
        source = f"{board_source}；东方财富板块主题基金接口无返回；未发现名称包含 {', '.join(tokens) or theme} 的公募基金"
    return items, source


def discover_theme_pool(fetcher: Fetcher, theme: str) -> tuple[list[PoolItem], str]:
    items, source = discover_concept_pool(fetcher, theme)
    if items:
        return items, source
    return [], f"未在本地产品池中发现主题；{source}；请提供该主题基金产品池"


def choose_by_name(input_name: str, input_code: str, candidates: list[Candidate]) -> tuple[Candidate | None, str]:
    if not candidates:
        return None, "无法匹配"
    target_norm = normalize_name(input_name)
    target_base = base_key(input_name)
    target_share = explicit_share_class(input_name)
    exact = [c for c in candidates if normalize_name(c.name) == target_norm]
    same_base = [c for c in candidates if c.base_key == target_base]
    chosen_pool = exact or same_base
    if chosen_pool:
        if input_code:
            for candidate in chosen_pool:
                if candidate.code == input_code:
                    return candidate, ""
        if target_share:
            same_share = [c for c in chosen_pool if c.share_class == target_share]
            if len(same_share) == 1:
                return same_share[0], ""
        if len(chosen_pool) == 1:
            return chosen_pool[0], ""
        non_c = [c for c in chosen_pool if c.share_class == "A"]
        if len(non_c) == 1:
            return non_c[0], ""
        return None, "名称多候选"
    if target_share:
        same_share = [c for c in candidates if c.share_class == target_share]
        if len(same_share) == 1:
            return same_share[0], "名称未精确匹配，使用同份额候选"
    if input_code:
        by_code = [c for c in candidates if c.code == input_code]
        if by_code:
            return by_code[0], "名称未精确匹配，使用代码候选"
    if len(candidates) == 1:
        return candidates[0], "名称模糊匹配"
    return None, "名称多候选"


def choose_fund(fetcher: Fetcher, input_name: str, input_code: str) -> tuple[Candidate | None, str]:
    if input_code and not re.fullmatch(r"\d{6}", input_code):
        candidates = fetcher.search(input_name)
        chosen, issue = choose_by_name(input_name, "", candidates)
        return chosen, f"代码格式无效；{issue}" if issue else "代码格式无效，使用名称匹配"
    if input_code:
        by_code = fetcher.get_by_code(input_code)
        if by_code:
            if base_key(input_name) != by_code.base_key:
                return by_code, "名称代码不一致，已按代码优先"
            return by_code, ""
        candidates = fetcher.search(input_name)
        chosen, issue = choose_by_name(input_name, "", candidates)
        if chosen:
            return chosen, "代码无法匹配，使用名称匹配" if not issue else f"代码无法匹配；{issue}"
        return None, "代码无法匹配；名称无法匹配"
    candidates = fetcher.search(input_name)
    chosen, issue = choose_by_name(input_name, "", candidates)
    return chosen, issue


def find_share_family(fetcher: Fetcher, candidate: Candidate) -> list[Candidate]:
    keywords = [candidate.name, strip_share_suffix(candidate.name)]
    seen: dict[str, Candidate] = {candidate.code: candidate}
    target_base = candidate.base_key
    for keyword in keywords:
        try:
            for item in fetcher.search(keyword):
                if item.base_key == target_base:
                    seen[item.code] = item
        except Exception:
            continue
    return sorted(seen.values(), key=lambda c: (c.share_class != "A", c.share_class, c.code))


def strip_share_suffix(value: str) -> str:
    return SHARE_SUFFIX_RE.sub("", normalize_space(value)).strip()


def choose_analysis_share(family: list[Candidate], fallback: Candidate) -> tuple[Candidate, str | None]:
    return fallback, None


def parse_js_array(text: str, var_name: str) -> Any:
    match = re.search(rf"var\s+{re.escape(var_name)}\s*=\s*(.*?);", text, re.S)
    if not match:
        return None
    return json.loads(match.group(1))


def millis_to_date(value: int | float) -> dt.date:
    return dt.datetime.fromtimestamp(float(value) / 1000).date()


def parse_acworth_trend(text: str) -> list[NavPoint]:
    data = parse_js_array(text, "Data_ACWorthTrend")
    points: list[NavPoint] = []
    if isinstance(data, list):
        for row in data:
            if isinstance(row, list) and len(row) >= 2 and row[1] not in (None, ""):
                points.append(NavPoint(date=millis_to_date(row[0]), value=float(row[1])))
    return points


def parse_networth_trend(text: str) -> list[NavPoint]:
    data = parse_js_array(text, "Data_netWorthTrend")
    points: list[NavPoint] = []
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and row.get("x") and row.get("y") not in (None, ""):
                points.append(NavPoint(date=millis_to_date(row["x"]), value=float(row["y"])))
    return points


def parse_scale_table(candidate: Candidate, text: str) -> ScalePoint:
    content_match = re.search(r'content:"(.*?)"', text, re.S)
    content = content_match.group(1) if content_match else text
    content = html.unescape(content.replace(r"\"", '"').replace(r"\/", "/"))
    row_match = re.search(r"<tbody>.*?<tr>(.*?)</tr>", content, re.S)
    if not row_match:
        return ScalePoint(code=candidate.code, name=candidate.name, date=None, value=None)
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row_match.group(1), re.S)
    clean = [re.sub(r"<.*?>", "", cell).strip() for cell in cells]
    if len(clean) < 5:
        return ScalePoint(code=candidate.code, name=candidate.name, date=None, value=None)
    scale_date = parse_date(clean[0])
    value = parse_float(clean[4])
    return ScalePoint(code=candidate.code, name=candidate.name, date=scale_date, value=value)


def parse_stage_return_table(text: str) -> dict[str, float]:
    content_match = re.search(r'content:"(.*?)"', text, re.S)
    content = content_match.group(1) if content_match else text
    content = html.unescape(content.replace(r"\"", '"').replace(r"\/", "/"))
    mapping = {
        "近1周": "1w",
        "近1月": "1m",
        "近3月": "3m",
        "近6月": "6m",
        "近1年": "1y",
        "今年来": "ytd",
    }
    returns: dict[str, float] = {}
    for block in re.findall(r"<ul[^>]*>(.*?)</ul>", content, re.S):
        cells = [re.sub(r"<.*?>", "", cell).strip() for cell in re.findall(r"<li[^>]*>(.*?)</li>", block, re.S)]
        if len(cells) < 2:
            continue
        period = mapping.get(cells[0])
        value = parse_float(cells[1])
        if period and value is not None:
            returns[period] = value
    return returns


def parse_float(value: str) -> float | None:
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if text in {"", "--", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def target_range(period: str, end: dt.date, start: dt.date | None) -> tuple[dt.date | None, dt.date]:
    if period == "custom":
        if start is None:
            raise ValueError("custom period requires --start")
        return start, end
    if period == "latest-day":
        return None, end
    if period == "1w":
        return end - dt.timedelta(days=7), end
    if period == "1m":
        return end - dt.timedelta(days=30), end
    if period == "3m":
        return end - dt.timedelta(days=91), end
    if period == "6m":
        return end - dt.timedelta(days=182), end
    if period == "1y":
        return end - dt.timedelta(days=365), end
    if period == "ytd":
        return dt.date(end.year, 1, 1), end
    raise ValueError(f"Unsupported period: {period}")


def find_at_or_before(points: list[NavPoint], target: dt.date) -> int | None:
    idx = None
    for i, point in enumerate(points):
        if point.date <= target:
            idx = i
        else:
            break
    return idx


def compute_metrics(points: list[NavPoint], period: str, start_arg: dt.date | None, end_arg: dt.date | None) -> tuple[float, float, dt.date, dt.date]:
    if len(points) < 2:
        raise ValueError("净值不足")
    end_target = end_arg or points[-1].date
    end_idx = find_at_or_before(points, end_target)
    if end_idx is None:
        raise ValueError("结束日无有效净值")
    effective_end = points[end_idx].date
    start_target, _ = target_range(period, effective_end, start_arg)
    if period == "latest-day":
        start_idx = end_idx - 1
    else:
        assert start_target is not None
        start_idx = find_at_or_before(points, start_target)
    if start_idx is None or start_idx < 0 or start_idx >= end_idx:
        raise ValueError("区间起始净值不足")
    start_point = points[start_idx]
    end_point = points[end_idx]
    ret = end_point.value / start_point.value - 1
    peak = points[start_idx].value
    max_dd = 0.0
    for point in points[start_idx : end_idx + 1]:
        peak = max(peak, point.value)
        if peak:
            max_dd = min(max_dd, point.value / peak - 1)
    return ret * 100, max_dd * 100, start_point.date, end_point.date


def merge_scale(fetcher: Fetcher, family: list[Candidate]) -> tuple[float | None, dt.date | None, list[str]]:
    points: list[ScalePoint] = []
    issues: list[str] = []
    for candidate in family:
        try:
            point = fetcher.fetch_scale(candidate)
            if point.value is not None:
                points.append(point)
        except Exception:
            issues.append(f"{candidate.code}规模接口异常")
    if not points:
        return None, None, issues + ["规模缺失"]
    total = sum(point.value or 0.0 for point in points)
    dates = [point.date for point in points if point.date is not None]
    scale_date = max(dates) if dates else None
    if len({point.date for point in points}) > 1:
        issues.append("份额规模报告期不一致")
    return total, scale_date, issues


def populate_display_returns(fetcher: Fetcher, result: ProductResult, code: str, points: list[NavPoint]) -> None:
    stage_returns = fetcher.fetch_stage_returns(code)
    latest_date = points[-1].date.isoformat() if points else ""
    for period in ["latest-day", "1w", "1m"]:
        if period in stage_returns:
            result.display_returns[period] = stage_returns[period]
            if period == "latest-day":
                result.display_return_dates[period] = latest_date
            continue
        try:
            ret, _, _, end_date = compute_metrics(points, period, None, None)
            result.display_returns[period] = ret
            if period == "latest-day":
                result.display_return_dates[period] = end_date.isoformat()
            result.issues.append("天天基金阶段涨幅缺失，使用净值序列计算")
        except Exception as exc:
            result.issues.append(f"{period}:{exc}")


def analyze_item(fetcher: Fetcher, item: PoolItem, period: str, start: dt.date | None, end: dt.date | None) -> ProductResult:
    result = ProductResult(theme=item.theme, input_name=item.input_name, input_code=item.input_code)
    try:
        chosen, issue = choose_fund(fetcher, item.input_name, item.input_code)
        if issue:
            result.issues.append(issue)
        if chosen is None:
            return result
        result.fund_code = chosen.code
        result.fund_name = chosen.name
        if item.input_code and chosen.code != item.input_code:
            result.conflict = f"参考文件代码{item.input_code} -> 名称匹配{chosen.code}"
        elif item.input_name and base_key(item.input_name) != chosen.base_key:
            result.conflict = f"参考文件名称“{item.input_name}” -> 代码对应“{chosen.name}”"
        family = find_share_family(fetcher, chosen)
        analysis_share, share_issue = choose_analysis_share(family, chosen)
        if share_issue:
            result.issues.append(share_issue)
        result.analysis_code = analysis_share.code
        result.analysis_name = analysis_share.name
        result.merged_scale, result.scale_date, scale_issues = merge_scale(fetcher, family)
        result.issues.extend(scale_issues)
        points, source_flag = fetcher.fetch_nav(analysis_share.code)
        if source_flag != "累计净值":
            result.issues.append("累计净值缺失")
        populate_display_returns(fetcher, result, analysis_share.code, points)
        result.return_pct, result.max_drawdown_pct, result.start_date, result.end_date = compute_metrics(points, period, start, end)
        stage_returns = fetcher.fetch_stage_returns(analysis_share.code)
        if period in stage_returns and start is None and end is None:
            result.return_pct = stage_returns[period]
        elif period in {"latest-day", "1w", "1m", "3m", "6m", "1y", "ytd"} and start is None and end is None:
            result.issues.append("天天基金阶段涨幅缺失，使用净值序列计算")
    except Exception as exc:  # noqa: BLE001 - keep product in output with issue marker
        result.issues.append(str(exc) or "接口异常")
    result.issues = dedupe(result.issues)
    return result


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value and value not in seen:
            output.append(value)
            seen.add(value)
    return output


def rank_results(results: list[ProductResult], sort_key: str, order: str | None) -> None:
    assign_metric_ranks(results)
    valid = [r for r in results if r.sort_value(sort_key) is not None]
    if order is None:
        reverse = sort_key in {"return", "drawdown", "scale"}
    else:
        reverse = order == "desc"
    valid.sort(key=lambda r: (r.sort_value(sort_key) is not None, r.sort_value(sort_key)), reverse=reverse)
    for idx, result in enumerate(valid, start=1):
        result.rank = idx


def assign_display_return_ranks(results: list[ProductResult]) -> None:
    for period in ["latest-day", "1w", "1m"]:
        valid = [r for r in results if r.display_returns.get(period) is not None]
        valid.sort(key=lambda r: r.display_returns[period], reverse=True)
        for idx, result in enumerate(valid, start=1):
            result.display_return_ranks[period] = idx


def assign_metric_ranks(results: list[ProductResult]) -> None:
    metric_specs = [
        ("return_pct", "return_rank", True),
        ("max_drawdown_pct", "drawdown_rank", True),
        ("merged_scale", "scale_rank", True),
    ]
    for value_attr, rank_attr, reverse in metric_specs:
        valid = [r for r in results if getattr(r, value_attr) is not None]
        valid.sort(key=lambda r: getattr(r, value_attr), reverse=reverse)
        for idx, result in enumerate(valid, start=1):
            setattr(result, rank_attr, idx)


def fmt_pct(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}%"


def fmt_num(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def fmt_date(value: dt.date | None) -> str:
    return "" if value is None else value.isoformat()


def row_dict(result: ProductResult) -> dict[str, str]:
    rank = "" if result.rank is None else str(result.rank)
    return_rank = "" if result.return_rank is None else str(result.return_rank)
    drawdown_rank = "" if result.drawdown_rank is None else str(result.drawdown_rank)
    return {
        "主题": result.theme,
        "排名": rank,
        "基金代码": result.fund_code,
        "基金名称": result.fund_name or result.input_name,
        "分析份额代码": result.analysis_code,
        "分析份额名称": result.analysis_name,
        "区间起始日": fmt_date(result.start_date),
        "区间结束日": fmt_date(result.end_date),
        "区间收益率": fmt_pct(result.return_pct),
        "区间收益率/排名": f"{fmt_pct(result.return_pct)} / {return_rank}" if result.return_pct is not None else "",
        "最大回撤": fmt_pct(result.max_drawdown_pct),
        "最大回撤/排名": f"{fmt_pct(result.max_drawdown_pct)} / {drawdown_rank}" if result.max_drawdown_pct is not None else "",
        "近1日/排名": fmt_display_return(result, "latest-day"),
        "近1周/排名": fmt_display_return(result, "1w"),
        "近1月/排名": fmt_display_return(result, "1m"),
        "合并份额规模(亿元)": fmt_num(result.merged_scale),
        "规模截止日": fmt_date(result.scale_date),
        "数据来源": result.source,
        "异常标记": "；".join(result.issues),
        "冲突说明": result.conflict,
    }


def fmt_display_return(result: ProductResult, period: str) -> str:
    value = result.display_returns.get(period)
    rank = result.display_return_ranks.get(period)
    if value is None:
        return ""
    return f"{fmt_pct(value)} / {rank}" if rank is not None else fmt_pct(value)


def latest_day_header(results_by_theme: dict[str, list[ProductResult]]) -> str:
    dates = sorted(
        {
            date
            for results in results_by_theme.values()
            for result in results
            for key, date in result.display_return_dates.items()
            if key == "latest-day" and date
        }
    )
    if dates:
        latest = dt.datetime.strptime(dates[-1], "%Y-%m-%d").strftime("%m/%d")
        return f"近1日({latest})/排名"
    return "近1日/排名"


def print_markdown(
    results_by_theme: dict[str, list[ProductResult]],
    period: str,
    top: int | None,
    detailed: bool = False,
    pool_sources: dict[str, str] | None = None,
) -> None:
    print(f"# 主题基金区间排名\n")
    print(f"- 区间参数：`{period}`")
    print("- 数据来源：天天基金/东方财富公开接口")
    print("- 口径：收益率优先取天天基金该代码页面阶段涨幅；规模合并同一产品多份额，单位为亿元。\n")
    for theme, results in results_by_theme.items():
        if pool_sources and pool_sources.get(theme):
            print(f"- 产品池来源：{pool_sources[theme]}\n")
        ranked = [r for r in results if r.rank is not None]
        ranked.sort(key=lambda r: r.rank or 10**9)
        display = ranked[:top] if top else ranked
        gaps = [r for r in results if r.rank is None or r.issues or r.conflict]
        print(f"## {theme}")
        if display:
            if detailed:
                headers = ["排名", "基金代码", "基金名称", "合并份额规模(亿元)", "区间收益率/排名", "最大回撤/排名", "规模截止日", "异常标记"]
            else:
                headers = ["排名", "基金代码", "基金名称", "合并份额规模(亿元)", latest_day_header({theme: results}), "近1周/排名", "近1月/排名", "异常标记"]
            print("| " + " | ".join(headers) + " |")
            print("| " + " | ".join(["---"] * len(headers)) + " |")
            for result in display:
                row = row_dict(result)
                values = []
                for header in headers:
                    if header.startswith("近1日("):
                        values.append(row["近1日/排名"])
                    else:
                        values.append(row[header])
                print("| " + " | ".join(values) + " |")
        else:
            print("无可排名产品。")
        if gaps:
            print("\n### 冲突与数据缺口")
            print("| 参考文件名称 | 参考文件代码 | 反查代码 | 异常标记 | 冲突说明 |")
            print("| --- | --- | --- | --- | --- |")
            for result in gaps:
                print(
                    "| "
                    + " | ".join(
                        [
                            result.input_name,
                            result.input_code,
                            result.fund_code,
                            "；".join(result.issues),
                            result.conflict,
                        ]
                    )
                    + " |"
                )
        print()


def write_export(path: Path, results: list[ProductResult]) -> None:
    rows = [row_dict(result) for result in results]
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else list(row_dict(ProductResult("", "", "")).keys()))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and rank theme fund products from public data.")
    parser.add_argument("--pool-reference", type=Path, default=DEFAULT_POOL_REFERENCE, help="Markdown product pool reference path.")
    parser.add_argument("--theme", default="all", help="Theme name in product-pools.md, or all.")
    parser.add_argument("--period", choices=["latest-day", "1w", "1m", "3m", "6m", "1y", "ytd", "custom"], default="1m")
    parser.add_argument("--start", help="Custom start date, YYYY-MM-DD.")
    parser.add_argument("--end", help="End date, YYYY-MM-DD. Defaults to latest available NAV date per fund.")
    parser.add_argument("--sort", choices=["return", "drawdown", "scale"], default="return")
    parser.add_argument("--order", choices=["asc", "desc"], help="Sort order. Defaults to descending.")
    parser.add_argument("--top", type=int, default=30, help="Top N ranked products to display per theme. Use 0 for all.")
    parser.add_argument("--export", type=Path, help="Optional CSV or JSON export path.")
    parser.add_argument("--detailed", action="store_true", help="Show the single selected period plus drawdown instead of the default 1d/1w/1m table.")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-products", type=int, help="Smoke-test limit per theme.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pool_path = args.pool_reference.expanduser().resolve()
    if not pool_path.exists():
        print(f"产品池参考文件不存在：{pool_path}", file=sys.stderr)
        return 2
    start = parse_date(args.start) if args.start else None
    end = parse_date(args.end) if args.end else None
    if args.period == "custom" and start is None:
        print("--period custom 必须提供 --start YYYY-MM-DD", file=sys.stderr)
        return 2
    pools = load_pool_reference(pool_path)
    pool_sources = {theme: f"本地产品池：{pool_path.name}" for theme in pools}
    fetcher = Fetcher(timeout=args.timeout, retries=args.retries)
    if args.theme != "all":
        if args.theme not in pools:
            discovered, source = discover_theme_pool(fetcher, args.theme)
            if not discovered:
                print(f"未找到主题：{args.theme}；{source}；本地可选主题：{', '.join(pools)}", file=sys.stderr)
                return 2
            pools = {args.theme: discovered}
            pool_sources = {args.theme: source}
        else:
            pools = {args.theme: pools[args.theme]}
            pool_sources = {args.theme: pool_sources[args.theme]}
    results_by_theme: dict[str, list[ProductResult]] = {}
    all_results: list[ProductResult] = []
    for theme, items in pools.items():
        unique_items = dedupe_pool_items(items)
        selected = unique_items[: args.max_products] if args.max_products else unique_items
        results = [analyze_item(fetcher, item, args.period, start, end) for item in selected]
        rank_results(results, args.sort, args.order)
        assign_display_return_ranks(results)
        results_by_theme[theme] = results
        all_results.extend(results)
    print_markdown(results_by_theme, args.period, None if args.top == 0 else args.top, detailed=args.detailed, pool_sources=pool_sources)
    if args.export:
        write_export(args.export.expanduser().resolve(), all_results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
