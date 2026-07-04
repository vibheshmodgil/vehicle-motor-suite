"""Allow `python -m vmi` to launch the app."""
from .app import TorqueSpeedApp
from . import theme
import seaborn as sns

theme.apply_appearance()
sns.set(style="whitegrid", context="talk", palette="deep")

if __name__ == "__main__":
    TorqueSpeedApp().mainloop()
