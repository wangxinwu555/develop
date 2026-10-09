"""根据当前环境锁定本项目的依赖闭包，不导出 Conda 环境里无关的包。

运行：python -m scripts.lock_dependencies。
仅用于维护锁文件；一般使用者直接安装 requirements.lock.txt 即可。
"""

from importlib.metadata import distribution

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from supportflow.config import ROOT


def main():
    pending = [("supportflow", {"dev"})]
    visited = {}
    versions = {}
    while pending:
        name, extras = pending.pop()
        key = canonicalize_name(name)
        if key in visited and extras <= visited[key]:
            continue
        extras = extras | visited.get(key, set())
        visited[key] = extras
        package = distribution(name)
        if key != "supportflow":
            versions[key] = package.version
        for raw in package.requires or []:
            req = Requirement(raw)
            if req.marker and not any(
                req.marker.evaluate({**default_environment(), "extra": extra})
                for extra in extras | {""}
            ):
                continue
            pending.append((req.name, set(req.extras)))
    text = "# Tested dependency closure, Python 3.11 / Windows; includes dev tools.\n"
    text += "\n".join(f"{name}=={version}" for name, version in sorted(versions.items())) + "\n"
    (ROOT / "requirements.lock.txt").write_text(text, encoding="utf-8")
    print(f"锁定 {len(versions)} 个项目依赖。")


if __name__ == "__main__":
    main()
