"""Initialize private credentials; deliberately never print the password."""

import argparse
from pathlib import Path

from policy_analysis.auth.initialize import initialize_admin
from policy_analysis.core.settings import load_settings

if __package__:
    from scripts.deployment import environment
else:
    from deployment import environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    environ = environment(root)
    settings = load_settings(root / "src/config/app.json", root, environ)
    created = initialize_admin(settings.auth.password_file, environ)
    print("已创建管理员凭据，请从本地 password.txt 获取初始口令。" if created else "已保留现有用户凭据。")


if __name__ == "__main__":
    main()
