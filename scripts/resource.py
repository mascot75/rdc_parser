# -*- coding: utf-8 -*-
"""资源分类匹配：从 mesh_export.csv + 资源分类标准.md 匹配资源类型。"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path


# ── 数据结构 ────────────────────────────────────────────────────────

@dataclass
class ResourceInfo:
    resource_type: str = ""
    resource_category: str = ""
    resource_path: str = ""
    matched_mesh_name: str = ""
    source: str = ""


@dataclass
class ResourceRule:
    resource_type: str
    resource_category: str
    path: str
    keyword_groups: list[list[str]]


@dataclass
class KeywordRule:
    resource_type: str
    resource_category: str
    keywords: list[str]


# ── 辅助函数 ────────────────────────────────────────────────────────

def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().replace("|", "/")


def normalize_asset_path(path: str) -> str:
    """将路径归一化：反斜杠转正斜杠，清理空白。"""
    return _clean_name(str(path or "").replace("\\", "/"))


def strip_mesh_suffix(name: str) -> str:
    """去掉 mesh 文件名的 .mesh/.fbx 后缀。"""
    name = Path(str(name).replace("\\", "/")).name
    lower = name.lower()
    if lower.endswith(".mesh"):
        return name[:-5]
    if lower.endswith(".fbx"):
        return name[:-4]
    return name


def _unescape_markdown_cell(text: str) -> str:
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!|])", r"\1", text or "")


# ── 数据加载 ────────────────────────────────────────────────────────

def load_mesh_export_csv(csv_path: Path | None) -> dict[str, list[tuple[str, int]]]:
    """加载 mesh_export.csv，返回 {mesh_name_lower: [(path, face_count)]}。"""
    if not csv_path or not csv_path.exists():
        return {}
    mesh_paths: dict[str, list[tuple[str, int]]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        path_field = "Path"
        face_field = "FaceCount"
        if reader.fieldnames and "Path" not in reader.fieldnames:
            path_field = (reader.fieldnames or [""])[0]
        for row in reader:
            path = normalize_asset_path(row.get(path_field, ""))
            if not path:
                continue
            mesh_name = strip_mesh_suffix(path)
            try:
                face_count = int(row.get(face_field, "0") or "0")
            except ValueError:
                face_count = 0
            if mesh_name:
                key = mesh_name.lower()
                if key not in mesh_paths:
                    mesh_paths[key] = []
                mesh_paths[key].append((path, face_count))
    return mesh_paths


def _split_keyword_groups(keyword_text: str) -> list[list[str]]:
    """解析关键字：/ 分隔 AND 组，| 分隔 OR 选项。"""
    keyword_text = _clean_name(_unescape_markdown_cell(keyword_text))
    if not keyword_text or "不限" in keyword_text:
        return []
    groups = []
    for group in keyword_text.split("/"):
        options = [x.strip().strip("`").lower() for x in group.split("|") if x.strip()]
        if options:
            groups.append(options)
    return groups


def parse_resource_standard_md(md_path: Path | None) -> list[ResourceRule]:
    """解析资源分类标准.md 中的分类规则表。"""
    if not md_path or not md_path.exists():
        return []
    text = md_path.read_text(encoding="utf-8-sig", errors="ignore")
    rules: list[ResourceRule] = []
    in_keyword_section = False
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            if "关键词规则" in line:
                in_keyword_section = True
            continue
        if in_keyword_section:
            break
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 4 or cols[0] == "类型":
            continue
        resource_type, resource_category, paths, keywords = [
            _unescape_markdown_cell(c) for c in cols[:4]
        ]
        paths = paths.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        for rule_path in [p.strip() for p in paths.splitlines() if p.strip()]:
            rules.append(ResourceRule(
                resource_type=resource_type,
                resource_category=resource_category,
                path=normalize_asset_path(rule_path).lower(),
                keyword_groups=_split_keyword_groups(keywords),
            ))
    return rules


def parse_keyword_rules_md(md_path: Path | None) -> list[KeywordRule]:
    """解析资源分类标准.md 中的关键词规则表。"""
    if not md_path or not md_path.exists():
        return []
    text = md_path.read_text(encoding="utf-8-sig", errors="ignore")
    rules: list[KeywordRule] = []
    in_keyword_section = False
    for line in text.splitlines():
        line = line.strip()
        if "关键词规则" in line:
            in_keyword_section = True
            continue
        if not in_keyword_section:
            continue
        if not line.startswith("|") or "---" in line:
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or cols[0] == "类型":
            continue
        resource_type, resource_category, keywords_text = [
            _unescape_markdown_cell(c) for c in cols[:3]
        ]
        keywords = [kw.strip().lower() for kw in keywords_text.split("|") if kw.strip()]
        if keywords:
            rules.append(KeywordRule(
                resource_type=resource_type,
                resource_category=resource_category,
                keywords=keywords,
            ))
    return rules


# ── 资源路径分类 ────────────────────────────────────────────────────

def _resource_rule_score(rule: ResourceRule, name_lower: str) -> tuple[int, int, int]:
    """评分规则（用于多条规则匹配时选最优）：
    1. LOD 变体匹配优先（_low/_mid 在关键字中 → +2 分）
    2. 关键字组数最少（规则越精确越好）
    3. 关键字总长度最短（避免过度匹配）
    """
    low_pos = name_lower.rfind("_low")
    mid_pos = name_lower.rfind("_mid")
    variant_score = 0
    flat_keywords = [kw for group in rule.keyword_groups for kw in group]
    if low_pos >= 0 or mid_pos >= 0:
        last_variant = "_low" if low_pos > mid_pos else "_mid"
        if last_variant == "_low" and any("_low" in kw for kw in flat_keywords):
            variant_score = 2
        elif last_variant == "_mid" and any("_mid" in kw for kw in flat_keywords):
            variant_score = 2
        elif any("_low" in kw or "_mid" in kw for kw in flat_keywords):
            variant_score = 1
    return (
        variant_score,
        len(rule.keyword_groups),
        sum(len(kw) for kw in flat_keywords),
    )


def classify_resource_path(mesh_name: str, full_path: str, rules: list[ResourceRule]) -> ResourceInfo:
    """根据路径和关键字规则分类资源。

    匹配优先级：
    1. 精确规则匹配（资源分类标准.md 中的路径+关键字规则）
    2. 路径启发式规则（/accessory/ → 挂件, /npc/ → NPC 等）
    3. 通用路径关键词（common_tree → 树, common_grass → 草 等）
    4. 回退到"未分类"
    """
    name_lower = mesh_name.lower()
    path = normalize_asset_path(full_path)
    path_lower = path.lower()

    # 1. 精确规则匹配
    matches: list[ResourceRule] = []
    for rule in rules:
        if rule.path and rule.path in path_lower:
            if not rule.keyword_groups or all(
                any(opt in name_lower for opt in group) for group in rule.keyword_groups
            ):
                matches.append(rule)
    if matches:
        rule = max(matches, key=lambda r: _resource_rule_score(r, name_lower))
        return ResourceInfo(
            rule.resource_type, rule.resource_category, path, mesh_name,
            "mesh_export+资源分类标准",
        )

    # 2. 路径启发式规则
    path_checks = [
        ("/accessory/", "Player", "挂件/配饰"),
        ("/weapon/", "武器", "武器"),
        ("/npc/", "NPC", "NPC"),
        ("/pet/", "宠物", "宠物"),
        ("/ride/", "坐骑", "坐骑"),
        ("/playersect/", "Player", "Player"),
        ("/player/", "Player", "Player"),
        ("/effect/", "特效", "特效"),
        ("/props/", "道具", "道具"),
    ]
    for needle, resource_type, category in path_checks:
        if needle in path_lower:
            return ResourceInfo(resource_type, category, path, mesh_name, "mesh_export路径规则")

    if "common_bush" in path_lower:
        return ResourceInfo("灌木", "灌木", path, mesh_name, "mesh_export路径规则")
    if "common_tree" in path_lower:
        return ResourceInfo("树", "树", path, mesh_name, "mesh_export路径规则")
    if "common_grass" in path_lower:
        return ResourceInfo("草", "草", path, mesh_name, "mesh_export路径规则")
    if "common_stone" in path_lower:
        return ResourceInfo("石头", "石头", path, mesh_name, "mesh_export路径规则")
    if "/map/" in path_lower or ("/temp/" in path_lower and "/maps/" in path_lower):
        return ResourceInfo("建筑/场景", "建筑/场景", path, mesh_name, "mesh_export路径规则")

    return ResourceInfo("未分类", "未分类", path, mesh_name, "mesh_export")


# ── ResourceMatcher ─────────────────────────────────────────────────

class ResourceMatcher:
    """从 VB/IB/shader/marker 文本中提取 mesh 名并匹配资源分类。"""

    def __init__(self, mesh_paths: dict[str, list[tuple[str, int]]], rules: list[ResourceRule], keyword_rules: list[KeywordRule] | None = None):
        self.mesh_paths = mesh_paths
        self.rules = rules
        self.keyword_rules = keyword_rules or []

    def _match_keyword_rules(self, *texts: str) -> ResourceInfo:
        """用关键词规则匹配。"""
        combined = " ".join(texts).lower()
        for rule in self.keyword_rules:
            if any(kw in combined for kw in rule.keywords):
                return ResourceInfo(
                    resource_type=rule.resource_type,
                    resource_category=rule.resource_category,
                    source="关键词规则",
                )
        return ResourceInfo()

    def _find_best_path(self, entries: list[tuple[str, int]], face_estimate: str) -> str:
        """从同名 mesh 的多个路径中，按面数匹配最接近的那个。

        同一个 mesh 名可能对应多个不同精度的资产（如高模/低模），
        用 Draw 参数面数与各路径的 FaceCount 取最近值。
        """
        if not entries:
            return ""
        if len(entries) == 1:
            return entries[0][0]
        try:
            target_faces = int(face_estimate)
        except (ValueError, TypeError):
            target_faces = 0
        if target_faces <= 0:
            return entries[0][0]
        best = min(entries, key=lambda e: abs(e[1] - target_faces))
        return best[0]

    def match(self, *texts: str, face_estimate: str = "") -> ResourceInfo:
        """从 VB/IB/shader/marker 等文本中提取 mesh 名，逐个查找 mesh_export.csv。

        匹配策略：
        1. 先尝试完整文本作为 mesh 名查找（如 VB 名本身就是 mesh 名）
        2. 再用正则提取 token 逐个查找（拆分 @、()、空格等特殊字符）
        3. 都找不到时回退到关键词规则匹配
        """
        if not self.mesh_paths:
            return self._match_keyword_rules(*texts)

        # 第一轮：完整文本匹配
        for text in texts:
            if not text:
                continue
            cleaned = strip_mesh_suffix(text.strip())
            if cleaned:
                entries = self.mesh_paths.get(cleaned.lower())
                if entries:
                    full_path = self._find_best_path(entries, face_estimate)
                    return classify_resource_path(cleaned, full_path, self.rules)

        # 第二轮：正则提取 token 匹配
        candidates: list[str] = []
        seen: set[str] = set()
        for text in texts:
            for token in re.findall(r"[A-Za-z0-9_\-./\\]+(?:\.mesh|\.fbx)?", text or ""):
                token = strip_mesh_suffix(token)
                if token and token.lower() not in seen:
                    candidates.append(token)
                    seen.add(token.lower())
        for candidate in candidates:
            entries = self.mesh_paths.get(candidate.lower())
            if entries:
                full_path = self._find_best_path(entries, face_estimate)
                return classify_resource_path(candidate, full_path, self.rules)

        return self._match_keyword_rules(*texts)


# ── 资产模块推断 ────────────────────────────────────────────────────

def normalize_asset_module(value: str) -> str:
    """将资源类型/分类归一化为资产模块。"""
    value = _clean_name(value)
    lower = value.lower()
    if not value or value in {"未分类", "未匹配", "未知"}:
        return ""
    if value in {"Player", "玩家"} or any(k in lower for k in ["player", "character", "js4character", "身体", "脸", "头发", "眼球", "裤子"]):
        return "角色"
    if value in {"NPC"} or "npc" in lower:
        return "NPC"
    if any(k in lower for k in ["weapon", "武器"]):
        return "武器"
    if any(k in lower for k in ["accessory", "挂件", "配饰"]):
        return "挂饰/配饰"
    if any(k in lower for k in ["ride", "坐骑"]):
        return "坐骑"
    if any(k in lower for k in ["pet", "宠物"]):
        return "宠物"
    if any(k in lower for k in ["effect", "特效"]):
        return "特效"
    if any(k in lower for k in ["ui", "界面"]):
        return "UI"
    if any(k in lower for k in ["建筑", "场景", "map", "model", "props", "道具"]):
        return "建筑/场景"
    if any(k in lower for k in ["terrain", "地形", "水体", "水面", "环境"]):
        return "环境"
    if any(k in lower for k in ["grass", "tree", "bush", "植被", "灌木", "树", "草"]):
        return "植被"
    if any(k in lower for k in ["stone", "石头"]):
        return "环境"
    return value


def resolve_asset_module(
    resource_info: ResourceInfo, mesh_type: str, vb: str, ib: str,
    shader: str, marker: str,
) -> tuple[str, str]:
    """综合推断资产模块：用资源分类结果归一化。"""
    module = normalize_asset_module(resource_info.resource_type)
    if module:
        return module, resource_info.source or "资源分类"
    module = normalize_asset_module(resource_info.resource_category)
    if module:
        return module, resource_info.source or "资源分类"
    return "未分类", "未分类"


def finalize_resource_info(
    resource_info: ResourceInfo, asset_module: str, asset_module_source: str,
    vb: str, ib: str, shader: str, marker: str,
) -> ResourceInfo:
    """补全未分类的资源信息。"""
    resource_type = _clean_name(resource_info.resource_type)
    resource_category = _clean_name(resource_info.resource_category)

    if not resource_type or resource_type == "未分类":
        resource_type = asset_module if asset_module != "未分类" else "未分类"
    if not resource_category or resource_category == "未分类":
        resource_category = asset_module if asset_module != "未分类" else "未分类"

    return ResourceInfo(
        resource_type=resource_type,
        resource_category=resource_category,
        resource_path=resource_info.resource_path,
        matched_mesh_name=resource_info.matched_mesh_name,
        source=resource_info.source or asset_module_source,
    )
