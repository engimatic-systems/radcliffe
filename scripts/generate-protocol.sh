#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
protoc_bin="${PROTOC:-protoc}"
proto_root="$root/packages/protocol/proto"
python_root="$root/packages/protocol/python/src/radcliffe_protocol"
android_root="$root/apps/android/app/src/main/java"

actual="$($protoc_bin --version)"
if [[ "$actual" != "libprotoc 33.6" ]]; then
  echo "Protocol generation requires libprotoc 33.6; found $actual" >&2
  exit 1
fi

"$protoc_bin" \
  --proto_path="$proto_root" \
  --python_out="$python_root" \
  --java_out=lite:"$android_root" \
  --kotlin_out=lite:"$android_root" \
  "$proto_root/radcliffe/v2/protocol.proto"

touch \
  "$python_root/radcliffe/__init__.py" \
  "$python_root/radcliffe/v2/__init__.py"

# protoc emits trailing spaces and a second final newline in some Java/Kotlin
# templates. Normalize generated text so Git's whitespace gate stays useful.
find "$android_root/works/ravd/radcliffe/protocol/v2" -type f \
  \( -name '*.java' -o -name '*.kt' \) -print0 \
  | xargs -0 perl -0pi -e 's/[ \t]+$//mg; s/\n{2,}\z/\n/'
