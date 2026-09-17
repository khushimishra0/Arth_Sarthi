"""The states and union territories, as `(label, value)` pairs.

The value is what a scheme's `eligibility.states` list holds and what the `profiles`
table stores; the label is what a human picks from. Both the Telegram keyboard and
the web dropdown render from this one list, so a state added here appears in both
without a second edit — and neither channel has to import the other to get it.
"""

from __future__ import annotations

INDIAN_STATES: tuple[tuple[str, str], ...] = (
    ("Andhra Pradesh", "andhra_pradesh"),
    ("Arunachal Pradesh", "arunachal_pradesh"),
    ("Assam", "assam"),
    ("Bihar", "bihar"),
    ("Chhattisgarh", "chhattisgarh"),
    ("Goa", "goa"),
    ("Gujarat", "gujarat"),
    ("Haryana", "haryana"),
    ("Himachal Pradesh", "himachal_pradesh"),
    ("Jharkhand", "jharkhand"),
    ("Karnataka", "karnataka"),
    ("Kerala", "kerala"),
    ("Madhya Pradesh", "madhya_pradesh"),
    ("Maharashtra", "maharashtra"),
    ("Manipur", "manipur"),
    ("Meghalaya", "meghalaya"),
    ("Mizoram", "mizoram"),
    ("Nagaland", "nagaland"),
    ("Odisha", "odisha"),
    ("Punjab", "punjab"),
    ("Rajasthan", "rajasthan"),
    ("Sikkim", "sikkim"),
    ("Tamil Nadu", "tamil_nadu"),
    ("Telangana", "telangana"),
    ("Tripura", "tripura"),
    ("Uttar Pradesh", "uttar_pradesh"),
    ("Uttarakhand", "uttarakhand"),
    ("West Bengal", "west_bengal"),
    ("Andaman & Nicobar", "andaman_nicobar"),
    ("Chandigarh", "chandigarh"),
    ("Dadra & Nagar Haveli and Daman & Diu", "dadra_nagar_haveli_daman_diu"),
    ("Delhi", "delhi"),
    ("Jammu & Kashmir", "jammu_kashmir"),
    ("Ladakh", "ladakh"),
    ("Lakshadweep", "lakshadweep"),
    ("Puducherry", "puducherry"),
)

STATE_LABELS: dict[str, str] = {value: label for label, value in INDIAN_STATES}
