"""Explicit operations and dependencies shared by native tools, CLI and Studio."""

WORKFLOW_OPERATIONS = {"workflow_start", "workflow_advance"}

CATALOG = {
    "workflow_start": ("开始固定重建流程", {}, ["workflow"], []),
    "workflow_advance": ("提交重建决策或恢复流程", {}, ["workflow"], []),
    "reference_fit": (
        "复算参考流程的观测拟合", {"perception": "perception"}, ["reference"],
        ["reference_case_root", "artisan_python"],
    ),
    "reference_code": (
        "按冻结蓝图生成资产", {"reference": "reference"}, ["reference"],
        ["construction_config", "artisan_python"],
    ),
    "reference_validate": (
        "执行参考流程的外观与验证", {"reference": "reference"}, ["reference"], [],
    ),
    "reference_review": (
        "独立审核参考流程的新候选", {"reference": "reference"}, ["reference"],
        ["construction_config", "artisan_python"],
    ),
    "ingest": ("准备输入", {}, ["images"], []),
    "inventory": ("识别候选物体", {"images": "images"}, ["inventory"], ["perception_config"]),
    "segment": (
        "分割物体",
        {"images": "images", "inventory": "inventory"},
        ["masks"],
        ["perception_config"],
    ),
    "label": (
        "理解物体用途",
        {"images": "images", "inventory": "inventory", "masks": "masks"},
        ["perception"],
        ["perception_config"],
    ),
    "perceive": ("识别图片或视频", {}, ["perception"], ["perception_config"]),
    "perception_edit": ("修正对象", {"perception": "perception"}, ["perception"], []),
    "evidence": (
        "整理对象证据",
        {"perception": "perception"},
        ["evidence"],
        ["construction_config"],
    ),
    "scope": ("确定重建范围", {"evidence": "evidence"}, ["scope"], ["construction_config"]),
    "scope_apply": ("指定重建范围", {"evidence": "evidence"}, ["scope"], []),
    "research": (
        "查询参考资料",
        {"evidence": "evidence", "scope": "scope"},
        ["research"],
        ["construction_config"],
    ),
    "blueprint": (
        "设计物体结构",
        {"evidence": "evidence", "scope": "scope"},
        ["blueprint"],
        ["construction_config"],
    ),
    "blueprint_review": (
        "检查结构方案",
        {"blueprint": "blueprint", "evidence": "evidence"},
        ["blueprint_review"],
        ["construction_config"],
    ),
    "blueprint_repair": (
        "局部修订几何关系", {"blueprint": "blueprint", "evidence": "evidence"},
        ["blueprint", "constraint_repair"], ["construction_config"],
    ),
    "geometry": (
        "设计外形",
        {"blueprint": "blueprint", "evidence": "evidence"},
        ["recipe"],
        ["construction_config"],
    ),
    "assembly_audit": (
        "检查连接结构",
        {"blueprint": "blueprint", "recipe": "recipe", "evidence": "evidence"},
        ["assembly"],
        [],
    ),
    "geometry_audit": (
        "检查功能面与几何配合",
        {"blueprint": "blueprint", "recipe": "recipe"},
        ["geometric_verification"],
        [],
    ),
    "build": (
        "构建模型",
        {"blueprint": "blueprint", "recipe": "recipe", "blueprint_review": "blueprint_review"},
        ["built"],
        ["artisan_python"],
    ),
    "render": ("更新渲染", {"blueprint": "blueprint", "recipe": "recipe"}, ["renders"], []),
    "geometry_review": (
        "检查模型外观",
        {"blueprint": "blueprint", "recipe": "recipe", "built": "built", "evidence": "evidence"},
        ["assets"],
        ["construction_config"],
    ),
    "assets_merge": ("合并对象资产", {"evidence": "evidence"}, ["assets"], []),
    "scene": ("组合场景", {"assets": "assets", "evidence": "evidence"}, ["scene"], []),
    "physics": (
        "执行物理验证",
        {"scene": "scene", "assets": "assets"},
        ["physics"],
        ["isaac_python"],
    ),
    "background": (
        "准备背景",
        {"perception": "perception"},
        ["background"],
        ["construction_config"],
    ),
    "cameras": ("重建相机", {"background": "background"}, ["cameras"], ["construction_config"]),
    "gaussian": ("重建背景场景", {"cameras": "cameras"}, ["gaussian"], ["construction_config"]),
    "overview": (
        "生成场景总览",
        {"gaussian": "gaussian", "cameras": "cameras"},
        ["overview"],
        ["construction_config"],
    ),
    "task_review": (
        "核对交付范围",
        {
            "assets": "assets",
            "physics": "physics",
            "evidence": "evidence",
            "blueprint": "blueprint",
        },
        ["task_review"],
        ["construction_config"],
    ),
    "export": (
        "打包交付",
        {"scene": "scene", "assets": "assets"},
        ["delivery"],
        ["artisan_python"],
    ),
}


def describe():
    return [
        {"id": key, "title": row[0], "inputs": row[1], "outputs": row[2]}
        for key, row in CATALOG.items()
    ]


def requirements(operation, settings):
    if operation not in CATALOG:
        raise ValueError("未注册的组件操作")
    fields = CATALOG[operation][3]
    missing = [
        key for key in fields if not getattr(settings, key) or not (
            getattr(settings, key).is_dir() if key == "reference_case_root"
            else getattr(settings, key).is_file()
        )
    ]
    if operation in {"build", "render"}:
        import shutil

        if not settings.blender and not shutil.which("blender"):
            missing.append("blender")
    return missing
