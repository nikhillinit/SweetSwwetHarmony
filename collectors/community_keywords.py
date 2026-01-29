"""
Shared Keywords for Community Signal Collectors

Aligned with Press On Ventures' Consumer Investment Thesis:
- Consumer CPG: Food, beverage, snacks, beauty, personal care
- Consumer Health Tech: Fitness, wellness, mental health, supplements, wearables
- Travel & Hospitality: Travel booking, hospitality tech, restaurants
- Consumer Marketplaces: Consumer-facing two-sided markets

Used by: Reddit, Telegram, Discord collectors
"""

# =============================================================================
# THESIS-ALIGNED CONSUMER KEYWORDS
# =============================================================================

# Consumer CPG: Food, beverage, snacks, beauty, personal care, household products
CONSUMER_CPG_KEYWORDS = [
    # Food & Beverage
    "food", "beverage", "meal", "snack", "drink", "grocery",
    "organic", "vegan", "plant-based", "recipe", "healthy",
    "meal kit", "food delivery", "meal delivery", "meal prep",
    "coffee", "tea", "juice", "kombucha", "protein",

    # Beauty & Personal Care
    "beauty", "skincare", "cosmetics", "makeup", "personal care",
    "haircare", "fragrance", "clean beauty", "sustainable beauty",

    # Household Products
    "household", "cleaning", "home goods", "sustainable products",

    # D2C/CPG Indicators
    "d2c", "dtc", "direct to consumer", "cpg", "brand",
    "subscription box", "consumer brand",
]

# Consumer Health Tech: Fitness, wellness, mental health, supplements, wearables
CONSUMER_HEALTH_TECH_KEYWORDS = [
    # Fitness
    "fitness", "workout", "exercise", "gym", "training",
    "fitness app", "workout app", "personal trainer",

    # Wellness
    "wellness", "meditation", "sleep", "mindfulness",
    "wellness app", "wellness platform", "self-care",

    # Mental Health
    "mental health", "therapy", "counseling", "mental wellness",
    "anxiety", "stress", "mental health app", "therapy app",

    # Digital Health (expanded)
    "digital health", "telehealth", "telemedicine", "healthtech",
    "health tech", "healthcare", "patient", "clinical",
    "remote monitoring", "health tracking", "health data",

    # Supplements & Nutrition
    "supplements", "vitamins", "nutrition", "protein",
    "probiotics", "nutraceuticals",

    # Wearables
    "wearable", "fitness tracker", "smart watch", "health tracker",
    "biometrics", "health wearable",

    # Specific Conditions
    "chronic", "diabetes", "heart health", "fertility",
    "women's health", "men's health", "aging", "longevity",
]

# Travel & Hospitality: Travel booking, hospitality tech, restaurants, experiences
TRAVEL_HOSPITALITY_KEYWORDS = [
    # Travel
    "travel", "vacation", "trip", "tourism", "destination",
    "travel booking", "travel app", "travel platform",
    "flights", "hotels", "airbnb", "short-term rental",

    # Hospitality
    "hospitality", "hotel", "lodging", "accommodation",
    "hospitality tech", "hotel tech", "resort",

    # Restaurants & Food Service
    "restaurant", "dining", "food service", "reservation",
    "restaurant tech", "food delivery", "takeout",

    # Experiences
    "experiences", "activities", "tours", "events",
    "experience booking", "local experiences",
]

# Consumer Marketplaces: Consumer-facing two-sided markets
CONSUMER_MARKETPLACE_KEYWORDS = [
    # Marketplace Types
    "marketplace", "e-commerce", "shopping", "retail",
    "peer-to-peer", "p2p", "c2c", "two-sided market",

    # Specific Verticals
    "delivery", "logistics", "on-demand",
    "resale", "secondhand", "vintage", "thrift",
    "rental", "sharing economy",

    # Consumer App Indicators
    "consumer app", "mobile app", "app",
    "subscription", "membership",
    "social commerce", "live shopping",
]

# Startup & Funding Indicators (signal strength boosters)
STARTUP_INDICATORS = [
    # Launch Indicators
    "launched", "launching", "just launched",
    "built", "i built", "we built", "shipped",
    "created", "i created", "we created",
    "introducing", "announcing", "new product",
    "mvp", "beta", "live now",

    # Startup Status
    "startup", "founder", "ceo", "co-founder",
    "early stage", "pre-seed", "seed", "series a",

    # Funding
    "raised", "funding", "investment", "round",
    "backed", "investors", "valuation",

    # Community Mentions
    "product hunt", "y combinator", "yc",
    "techstars", "accelerator", "incubator",
    "show hn", "hacker news",
]

# =============================================================================
# EXCLUSION KEYWORDS (Negative signals)
# =============================================================================

EXCLUSION_KEYWORDS = [
    # B2B/Enterprise (excluded from thesis)
    "enterprise", "b2b", "saas", "developer",
    "api", "devops", "infrastructure", "backend",
    "data platform", "analytics platform",

    # Crypto/Web3 (excluded from thesis)
    "blockchain", "crypto", "web3", "nft", "defi",
    "token", "decentralized",

    # Other Exclusions
    "consulting", "agency", "services firm",
    "series b", "series c", "late stage",
]

# =============================================================================
# COMBINED KEYWORD SETS
# =============================================================================

# All positive consumer keywords (union of all thesis categories)
ALL_CONSUMER_KEYWORDS = (
    CONSUMER_CPG_KEYWORDS +
    CONSUMER_HEALTH_TECH_KEYWORDS +
    TRAVEL_HOSPITALITY_KEYWORDS +
    CONSUMER_MARKETPLACE_KEYWORDS +
    STARTUP_INDICATORS
)

# Deduplicated list
ALL_CONSUMER_KEYWORDS = list(set(ALL_CONSUMER_KEYWORDS))

# High-value keywords (strong thesis signals)
HIGH_VALUE_KEYWORDS = [
    # CPG
    "meal kit", "meal delivery", "d2c", "dtc", "consumer brand",
    "beauty brand", "skincare brand", "food brand",

    # Health Tech
    "fitness app", "wellness app", "mental health app",
    "digital health", "telehealth", "health tech",
    "therapy app", "meditation app",

    # Travel
    "travel booking", "hospitality tech", "experience booking",

    # Marketplace
    "consumer marketplace", "peer-to-peer",

    # Funding (strong signals)
    "raised", "funding", "series a", "seed round",
    "launched", "just launched",
]

# Negative signals (fraud, scam indicators)
NEGATIVE_SENTIMENT_KEYWORDS = [
    "scam", "fraud", "fraudulent", "ponzi", "pyramid scheme",
    "lawsuit", "sued", "criminal", "illegal",
    "shutdown", "shut down", "failed", "bankrupt",
    "terrible", "awful", "horrible", "worst",
]
