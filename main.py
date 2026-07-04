"""
Entry point for the Vehicle <-> Motor Integration Suite.

Run with:   python main.py
        or: python -m vmi

Optional assets (place next to this file if you have them):
  - std_motor_data_sample.json   (standard-motor library; created/updated by the app)
  - tvs_logo.webp, motor.jpg     (brand images shown in the header)
The app now starts fine even if these are missing.
"""

from vmi import TorqueSpeedApp, theme

# Keep the original plot styling (seaborn "talk"/whitegrid) for the figures,
# applied on top of the modern CustomTkinter shell.
import seaborn as sns

theme.apply_appearance()
sns.set(style="whitegrid", context="talk", palette="deep")


def main():
    app = TorqueSpeedApp()
    app.mainloop()


if __name__ == "__main__":
    main()
