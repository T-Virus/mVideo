"""宠物内容系统常量：分类、风险分级、审核规则、身份约束。

对应任务书模块 3/4：所有枚举集中定义，避免散落在代码里。
"""
from __future__ import annotations

# ---------- 动物类型 ----------
ANIMAL_CATS = "cat"
ANIMAL_DOGS = "dog"
ANIMAL_BOTH = "both"
ANIMALS = (ANIMAL_CATS, ANIMAL_DOGS, ANIMAL_BOTH)
ANIMAL_LABELS = {ANIMAL_CATS: "猫咪", ANIMAL_DOGS: "狗狗", ANIMAL_BOTH: "猫狗"}

# ---------- 内容分类（任务书模块 3 的 8 类）----------
CATEGORY_CAT_HEALTH = "cat_health"
CATEGORY_DOG_HEALTH = "dog_health"
CATEGORY_CAT_BEHAVIOR = "cat_behavior"
CATEGORY_DOG_BEHAVIOR = "dog_behavior"
CATEGORY_COMPARISON = "comparison"
CATEGORY_ENTERTAINMENT = "entertainment"
CATEGORY_VET_DIARY = "vet_diary"
CATEGORY_PRODUCT_LIFE = "product_life"

CATEGORIES = (
    CATEGORY_CAT_HEALTH,
    CATEGORY_DOG_HEALTH,
    CATEGORY_CAT_BEHAVIOR,
    CATEGORY_DOG_BEHAVIOR,
    CATEGORY_COMPARISON,
    CATEGORY_ENTERTAINMENT,
    CATEGORY_VET_DIARY,
    CATEGORY_PRODUCT_LIFE,
)
CATEGORY_LABELS = {
    CATEGORY_CAT_HEALTH: "猫咪健康科普",
    CATEGORY_DOG_HEALTH: "狗狗健康科普",
    CATEGORY_CAT_BEHAVIOR: "猫咪行为",
    CATEGORY_DOG_BEHAVIOR: "狗狗行为",
    CATEGORY_COMPARISON: "猫狗趣味对比",
    CATEGORY_ENTERTAINMENT: "萌宠娱乐",
    CATEGORY_VET_DIARY: "兽医学习日记",
    CATEGORY_PRODUCT_LIFE: "宠物用品与生活",
}

# 内容类型：用于"科普/娱乐/用品"筛选
CONTENT_SCIENCE = "science"
CONTENT_ENTERTAINMENT = "entertainment"
CONTENT_PRODUCT = "product"
CONTENT_TYPES = (CONTENT_SCIENCE, CONTENT_ENTERTAINMENT, CONTENT_PRODUCT)
CONTENT_TYPE_LABELS = {
    CONTENT_SCIENCE: "科普",
    CONTENT_ENTERTAINMENT: "娱乐",
    CONTENT_PRODUCT: "用品",
}

# 分类 → 内容类型默认映射（供 AI 生成与筛选）
CATEGORY_TO_CONTENT_TYPE = {
    CATEGORY_CAT_HEALTH: CONTENT_SCIENCE,
    CATEGORY_DOG_HEALTH: CONTENT_SCIENCE,
    CATEGORY_CAT_BEHAVIOR: CONTENT_SCIENCE,
    CATEGORY_DOG_BEHAVIOR: CONTENT_SCIENCE,
    CATEGORY_COMPARISON: CONTENT_ENTERTAINMENT,
    CATEGORY_ENTERTAINMENT: CONTENT_ENTERTAINMENT,
    CATEGORY_VET_DIARY: CONTENT_SCIENCE,
    CATEGORY_PRODUCT_LIFE: CONTENT_PRODUCT,
}

# 分类 → 默认动物类型
CATEGORY_TO_ANIMAL = {
    CATEGORY_CAT_HEALTH: ANIMAL_CATS,
    CATEGORY_DOG_HEALTH: ANIMAL_DOGS,
    CATEGORY_CAT_BEHAVIOR: ANIMAL_CATS,
    CATEGORY_DOG_BEHAVIOR: ANIMAL_DOGS,
    CATEGORY_COMPARISON: ANIMAL_BOTH,
    CATEGORY_ENTERTAINMENT: ANIMAL_BOTH,
    CATEGORY_VET_DIARY: ANIMAL_BOTH,
    CATEGORY_PRODUCT_LIFE: ANIMAL_BOTH,
}

# ---------- 选题状态 ----------
TOPIC_STATUS_IDEA = "idea"
TOPIC_STATUS_SELECTED = "selected"
TOPIC_STATUS_SCRIPTED = "scripted"
TOPIC_STATUS_PUBLISHED = "published"
TOPIC_STATUS_ARCHIVED = "archived"
TOPIC_STATUSES = (
    TOPIC_STATUS_IDEA,
    TOPIC_STATUS_SELECTED,
    TOPIC_STATUS_SCRIPTED,
    TOPIC_STATUS_PUBLISHED,
    TOPIC_STATUS_ARCHIVED,
)

# ---------- 知识风险分级（任务书模块 4）----------
RISK_A = "A"  # 普通行为和生活知识
RISK_B = "B"  # 营养、疾病、护理等需要认真核对
RISK_C = "C"  # 药物、剂量、中毒、急症和治疗建议等高风险
RISK_LEVELS = (RISK_A, RISK_B, RISK_C)
RISK_LABELS = {
    RISK_A: "A-普通行为生活知识",
    RISK_B: "B-营养疾病护理（需认真核对）",
    RISK_C: "C-药物剂量中毒急症（高风险）",
}

# 高风险关键词：命中即强制分级为 C
RISK_C_KEYWORDS = (
    "用药", "药物", "剂量", "服药", "喂药", "中毒", "急症", "急诊", "急救",
    "诊断", "治疗", "处方", "抗生素", "疫苗", "抽搐", "呕吐带血", "呼吸困难",
    "误食", "药物过量", "副作用", "禁忌", "皮下注射", "输液", "手术",
)
# 中风险关键词：命中即强制分级为至少 B
RISK_B_KEYWORDS = (
    "营养", "饮食", "喂养", "疾病", "护理", "症状", "预防", "驱虫",
    "绝育", "体重", "肠胃", "皮肤", "过敏", "传染", "感染",
)

# ---------- 审核状态 ----------
REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"
REVIEW_STATUSES = (REVIEW_PENDING, REVIEW_APPROVED, REVIEW_REJECTED)

# ---------- 身份约束（任务书模块 4）----------
IDENTITY_LEARNER = "learner"  # 兽医学习者（真实身份）
FORBIDDEN_IDENTITY_TERMS = (
    "执业兽医", "兽医专家", "资深兽医", "主治兽医", "专业兽医", "兽医师",
    "执业兽医师", "我是医生", "临床兽医", " veterinarian with license",
)

# ---------- 急症提示（任务书模块 4 规则 4）----------
EMERGENCY_DISCLAIMER = (
    "注意：如果宠物出现急症、中毒或危及生命的情况，请立即联系当地兽医或动物急诊，"
    "不要依赖本内容进行自行处置。"
)

# ---------- 语言 ----------
LANG_ZH = "zh"
LANG_EN = "en"
LANGUAGES = (LANG_ZH, LANG_EN)
