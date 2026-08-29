#!/usr/bin/env bash
set -euo pipefail

package_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$package_dir/../.." && pwd)"
output_dir="$package_dir/output"
work_dir="$package_dir/work"
build_python="${BUILD_PYTHON:-$package_dir/.venv/bin/python}"

if [[ ! -x "$build_python" ]]; then
  echo "Missing build Python: $build_python" >&2
  echo "Create the build environment and install the project plus PyInstaller first." >&2
  exit 1
fi

rm -rf "$work_dir" "$output_dir/SeeFurther" "$output_dir/SeeFurther-v1.0.0-linux-x86_64.tar.gz"
mkdir -p "$work_dir" "$output_dir"

cd "$repo_root"
"$build_python" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name SeeFurther \
  --distpath "$output_dir" \
  --workpath "$work_dir" \
  --specpath "$work_dir" \
  --add-data "$repo_root/gateway/static:gateway/static" \
  --add-data "$repo_root/skills/builtin:skills/builtin" \
  --add-data "$repo_root/agents/profiles.json:agents" \
  --collect-all fitz \
  --collect-all pymupdf \
  --collect-all lark_oapi \
  --collect-submodules handlers \
  --collect-submodules skills \
  "$package_dir/launcher.py"

cp "$package_dir/README.txt" "$output_dir/SeeFurther/README.txt"
tar -C "$output_dir" -czf "$output_dir/SeeFurther-v1.0.0-linux-x86_64.tar.gz" SeeFurther
sha256sum "$output_dir/SeeFurther-v1.0.0-linux-x86_64.tar.gz"
