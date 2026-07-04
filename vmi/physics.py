"""Calculation core. Functions and the reference lookup table are copied
VERBATIM from the original program -- the numbers are unchanged."""

import pandas as pd
import numpy as np  # noqa: F401  (kept for parity / downstream use)

data = {
    "m_ref_min": [0, 25, 35, 45, 55, 65, 75, 85, 95, 105, 115, 125, 135, 145, 155, 165, 175, 185, 195, 205, 215, 225, 235, 245, 255, 265, 275, 285, 295, 305, 315, 325, 335, 345, 355, 365, 375, 385, 395, 405, 415, 425, 435, 445, 455, 465, 475, 485, 495],
    "m_ref_max": [25, 35, 45, 55, 65, 75, 85, 95, 105, 115, 125, 135, 145, 155, 165, 175, 185, 195, 205, 215, 225, 235, 245, 255, 265, 275, 285, 295, 305, 315, 325, 335, 345, 355, 365, 375, 385, 395, 405, 415, 425, 435, 445, 455, 465, 475, 485, 495, 505],
    "m_i": [20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230, 240, 250, 260, 270, 280, 290, 300, 310, 320, 330, 340, 350, 360, 370, 380, 390, 400, 410, 420, 430, 440, 450, 460, 470, 480, 490, 500],
    "a": [1.8, 2.6, 3.5, 4.4, 5.3, 6.8, 7.0, 7.9, 8.8, 9.7, 10.6, 11.4, 12.3, 13.2, 14.1, 15.0, 15.8, 16.7, 17.6, 18.5, 19.4, 20.2, 21.1, 22.0, 22.9, 23.8, 24.6, 25.5, 26.4, 27.3, 28.2, 29.0, 29.9, 30.8, 31.7, 32.6, 33.4, 34.3, 35.2, 36.1, 37.0, 37.8, 38.7, 39.6, 40.5, 41.4, 42.2, 43.1, 44.0],
    "b": [0.0203, 0.0205, 0.0206, 0.0208, 0.0209, 0.0211, 0.0212, 0.0214, 0.0215, 0.0217, 0.0218, 0.0220, 0.0221, 0.0223, 0.0224, 0.0226, 0.0227, 0.0229, 0.0230, 0.0232, 0.0233, 0.0235, 0.0236, 0.0238, 0.0239, 0.0241, 0.0242, 0.0244, 0.0245, 0.0247, 0.0248, 0.0250, 0.0251, 0.0253, 0.0254, 0.0256, 0.0257, 0.0259, 0.0260, 0.0262, 0.0263, 0.0265, 0.0266, 0.0268, 0.0269, 0.0271, 0.0272, 0.0274, 0.0275]
}

df = pd.DataFrame(data)
g = 9.81  # Gravity (m/sÂ²)

def calculate_crr_cd_a(m_ref, rear_load_ratio=0.5, ambient_temp=25, ambient_pressure=1.01325, crr=None, cd_a=None):
    row = df[(df['m_ref_min'] < m_ref) & (df['m_ref_max'] >= m_ref)]
    has_manual_crr = crr is not None
    has_manual_cda = cd_a is not None
    has_lookup = not row.empty

    if not has_lookup and not (has_manual_crr and has_manual_cda):
        raise ValueError(
            "Reference mass out of range for auto calculation. "
            "Please enter both Crr and CdA manually."
        )

    if has_lookup:
        m_i, a, b = row.iloc[0][['m_i', 'a', 'b']]
    else:
        # For out-of-table masses, allow manual Crr/CdA and use entered mass directly.
        m_i = float(m_ref)
        a = None
        b = None

    if has_manual_crr:
        crr_value = float(crr)
    else:
        crr_value = float(a) / (float(m_i) * rear_load_ratio * g)

    if has_manual_cda:
        cda_value = float(cd_a)
    else:
        cda_value = (2 * float(b) * (3.6 ** 2) * (273 + ambient_temp) * 287) / (ambient_pressure * 100000)

    return {"m_i": float(m_i), "Crr": round(crr_value, 5), "CdA": round(cda_value, 5)}
