# -*- coding: utf-8 -*-
"""Mesh 类型分类与 Pass 名称归一化。"""

from __future__ import annotations


def classify(marker: str, shader: str, rt: str) -> str:
    """根据 marker/shader/rt 判断 Mesh 渲染类型。

    优先级从高到低匹配：UI 类 → 不透明 → 阴影 → 透明 → 后处理 → 未知。
    marker 包含渲染 Pass 层级信息（如 DrawOpaqueObjects/MainLightShadow），
    shader 和 rt 用于辅助判断（如 TextMeshPro → UI）。
    """
    text = f"{marker} {shader} {rt}"
    if "UICaptionRenderPass" in text or "UIR.Draw" in text:
        return "UI Caption / 头顶信息"
    if "TextMeshPro" in text or "GUITexture" in text or "GUI.Repaint" in text:
        return "UI / 字体 / GUI"
    if "Canvas.RenderSubBatch" in text or "DrawTransparentObjectsGammaSpace" in text:
        return "UI / Canvas 透明层"
    if "DrawTransparentObjects" in text:
        return "场景透明物 / 特效"
    if "DrawOpaqueObjects" in text or "Render GBuffer" in text or "Render Opaques Forward Only" in text:
        return "场景不透明 Mesh"
    if "DepthNormalOnlyPrepass" in text:
        return "DepthNormal 预写 Mesh"
    if "Decal GBuffer" in text or "Decal" in text:
        return "Decal Mesh/投射"
    if "MainLightShadow" in text or "Shadows." in text:
        return "阴影 caster Mesh"
    if "AlphaTestDepthPrepass" in text:
        return "AlphaTest 深度预写 Mesh"
    if "MotionVectors" in text:
        return "运动向量 Mesh"
    if "DrawSubsurfaceScatteringObject" in text or "SubsurfaceScattering" in text:
        return "SSS 对象 Mesh"
    if "WaterPlanarReflection" in text:
        return "水面反射 Mesh"
    if "WaterSurface" in text or "WaterCaustic" in text:
        return "水面 / 焦散 Mesh"
    if "VirtualTextureRender" in text or "PhysicalTexture" in text:
        return "虚拟纹理反馈/物理页绘制"
    if "LightShaftsPass" in text:
        return "体积光/光束 Mesh"
    if "VolumetricClouds" in text:
        return "体积云 Pass"
    if "Bloom" in text:
        return "Bloom 后处理"
    if "SMAA" in text:
        return "SMAA 后处理"
    if "UberPost" in text or "CoreBlit" in text or "CopyDepth" in text:
        return "通用后处理/Blit"
    if "Draw" in marker:
        return "未细分 Mesh 绘制"
    return "未分类/未知"


# ── Pass 名称归一化 ──────────────────────────────────────────────────

PASS_NAME_PATTERNS = [
    "MainLightShadow", "DepthNormalOnlyPrepass", "Render GBuffer",
    "Render Opaques Forward Only", "DrawOpaqueObjects", "DrawTransparentObjects",
    "DrawReduceResTransparentPass", "ReduceResolutionTransparent",
    "AlphaTestDepthPrepass", "MotionVectors", "Decal GBuffer Render",
    "WaterPlanarReflection", "WaterSurfacePrePass", "WaterSurfaceCaustics",
    "WaterCausticDecalPass", "TerrainBlendPrePass", "UICaptionRenderPass",
    "UIR.DrawChain", "DrawTransparentObjectsGammaSpace", "HBAO", "SMAA",
    "SubsurfaceScattering", "Deferred Pass", "Deferred Shading",
    "LightShaftsPass", "VolumetricClouds", "Bloom", "CopyDepth",
    "UberPost", "CoreBlit", "FinalBlit",
]

PASS_NAME_ALIASES = {
    "Shadows.DrawSRPBatcher": "MainLightShadow",
    "Shadows.CullResults": "MainLightShadow",
    "ShadowPass": "MainLightShadow",
}


def short_marker(marker: str) -> str:
    """截取 marker 最后 4 层。"""
    parts = [p.strip() for p in marker.split("/") if p.strip()]
    return " / ".join(parts[-4:]) if len(parts) > 4 else marker


def normalize_pass_name(marker: str) -> str:
    """从 marker 层级中提取最内层有意义的 Pass 名称。

    marker 格式如 "Renderer / Pass / SubPass / DrawCall"，
    从最内层向外查找，匹配 PASS_NAME_PATTERNS 中的已知 Pass。
    特殊情况：Shadows.DrawSRPBatcher → MainLightShadow（别名映射）。
    """
    parts = [p.strip() for p in marker.split("/") if p.strip()]
    for part in reversed(parts):
        clean = part.split("{")[0].strip()
        for pattern in PASS_NAME_PATTERNS:
            if pattern.lower() in clean.lower():
                return pattern
    for part in reversed(parts):
        clean = part.split("{")[0].strip()
        if clean in PASS_NAME_ALIASES:
            return PASS_NAME_ALIASES[clean]
    if parts:
        last = parts[-1].split("{")[0].strip()
        if last and last.lower() not in {"gpu", "cputime", "frametime"}:
            return last
    return marker
