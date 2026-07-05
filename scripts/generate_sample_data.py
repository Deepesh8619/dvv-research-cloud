"""Generate realistic pharma survey data for MVP demo."""

import sys
from pathlib import Path

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.adapters.spss import write_sav
from src.core.models import DataMap, Variable, VarType

# Create realistic pharma survey data (n=500)
data = {
    "respondent_id": range(1, 501),
    "age_group": [i % 6 for i in range(500)],  # 0-5 codes for age groups
    "gender": [i % 2 for i in range(500)],  # 0-1 codes for gender
    "income": [i % 5 for i in range(500)],  # 0-4 codes for income
    "region": [i % 4 for i in range(500)],  # 0-3 codes for region
    "awareness_aspirin": [
        [1, 2, 3, 4, 5][i % 5] for i in range(500)
    ],  # 1=Not aware, 5=Very aware
    "awareness_tylenol": [
        [1, 2, 3, 4, 5][(i + 1) % 5] for i in range(500)
    ],
    "awareness_ibuprofen": [
        [1, 2, 3, 4, 5][(i + 2) % 5] for i in range(500)
    ],
    "purchase_intent_aspirin": [
        [1, 2, 3, 4, 5][(i + 1) % 5] for i in range(500)
    ],  # 1=Definitely not, 5=Definitely yes
    "purchase_intent_tylenol": [
        [1, 2, 3, 4, 5][(i + 2) % 5] for i in range(500)
    ],
    "purchase_intent_ibuprofen": [
        [1, 2, 3, 4, 5][i % 5] for i in range(500)
    ],
    "satisfaction_current": [
        [1, 2, 3, 4, 5][(i + 3) % 5] for i in range(500)
    ],  # 1=Very dissatisfied, 5=Very satisfied
    "recommendation_willingness": [
        [1, 2, 3, 4, 5][(i + 2) % 5] for i in range(500)
    ],  # 1=Definitely not, 5=Definitely yes
    "feedback": [
        "Works well" if i % 3 == 0 else "Too expensive" if i % 3 == 1 else "Good quality"
        for i in range(500)
    ],
}

df = pd.DataFrame(data)

# Create comprehensive DataMap
variables = [
    Variable("respondent_id", label="Respondent ID", type=VarType.NUMERIC),
    Variable(
        "age_group",
        label="Age Group",
        type=VarType.SINGLE,
        values={
            0: "18-24",
            1: "25-34",
            2: "35-44",
            3: "45-54",
            4: "55-64",
            5: "65+",
        },
    ),
    Variable(
        "gender",
        label="Gender",
        type=VarType.SINGLE,
        values={0: "Male", 1: "Female"},
    ),
    Variable(
        "income",
        label="Household Income",
        type=VarType.SINGLE,
        values={
            0: "<$30k",
            1: "$30-50k",
            2: "$50-75k",
            3: "$75-100k",
            4: "$100k+",
        },
    ),
    Variable(
        "region",
        label="Region",
        type=VarType.SINGLE,
        values={0: "North", 1: "South", 2: "East", 3: "West"},
    ),
    Variable(
        "awareness_aspirin",
        label="Awareness - Aspirin",
        type=VarType.SINGLE,
        values={
            1: "Not Aware",
            2: "Slightly Aware",
            3: "Moderately Aware",
            4: "Very Aware",
            5: "Extremely Aware",
        },
    ),
    Variable(
        "awareness_tylenol",
        label="Awareness - Tylenol",
        type=VarType.SINGLE,
        values={
            1: "Not Aware",
            2: "Slightly Aware",
            3: "Moderately Aware",
            4: "Very Aware",
            5: "Extremely Aware",
        },
    ),
    Variable(
        "awareness_ibuprofen",
        label="Awareness - Ibuprofen",
        type=VarType.SINGLE,
        values={
            1: "Not Aware",
            2: "Slightly Aware",
            3: "Moderately Aware",
            4: "Very Aware",
            5: "Extremely Aware",
        },
    ),
    Variable(
        "purchase_intent_aspirin",
        label="Purchase Intent - Aspirin",
        type=VarType.SINGLE,
        values={
            1: "Definitely Not",
            2: "Probably Not",
            3: "Neutral",
            4: "Probably Yes",
            5: "Definitely Yes",
        },
    ),
    Variable(
        "purchase_intent_tylenol",
        label="Purchase Intent - Tylenol",
        type=VarType.SINGLE,
        values={
            1: "Definitely Not",
            2: "Probably Not",
            3: "Neutral",
            4: "Probably Yes",
            5: "Definitely Yes",
        },
    ),
    Variable(
        "purchase_intent_ibuprofen",
        label="Purchase Intent - Ibuprofen",
        type=VarType.SINGLE,
        values={
            1: "Definitely Not",
            2: "Probably Not",
            3: "Neutral",
            4: "Probably Yes",
            5: "Definitely Yes",
        },
    ),
    Variable(
        "satisfaction_current",
        label="Satisfaction with Current Brand",
        type=VarType.SINGLE,
        values={
            1: "Very Dissatisfied",
            2: "Dissatisfied",
            3: "Neutral",
            4: "Satisfied",
            5: "Very Satisfied",
        },
    ),
    Variable(
        "recommendation_willingness",
        label="Likelihood to Recommend",
        type=VarType.SINGLE,
        values={
            1: "Definitely Not",
            2: "Probably Not",
            3: "Neutral",
            4: "Probably Yes",
            5: "Definitely Yes",
        },
    ),
    Variable("feedback", label="Open-End Feedback", type=VarType.TEXT),
]

datamap = DataMap()
for v in variables:
    datamap.add(v)

# Write to SPSS
output_path = Path(__file__).parent.parent / "data" / "pharma_study.sav"
output_path.parent.mkdir(parents=True, exist_ok=True)
write_sav(df, datamap, str(output_path))

print(f"✅ Generated sample pharma survey: {output_path}")
print(f"   n={len(df)} respondents, {len(variables)} variables")
