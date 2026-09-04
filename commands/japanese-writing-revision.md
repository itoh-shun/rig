---
description: "[experimental] owner-only の既存下書きを安全な stdin で渡し、独立検証済みの修正版を別ファイルへ保存する。"
argument-hint: "<absolute-draft-path> <absolute-new-output-path> --review-category ... --material-profile ..."
---

# rig/japanese-writing-revision — 下書きを別ファイルへ修正する

この command は明示的に選んだ場合だけ使う draft-revision pattern です。通常の chat や
`japanese-writing` の既定動作には影響しません。source と output は必ず別の絶対 path とし、既存の
output は拒否します。category は `--review-category general|incident_report|support_reply`、文体素材は
`--material-profile none|technical|conversation` から本文とは別に選びます。未知値・省略・pin 不備は
provider call 前に fail closed します。

この recipe は provider を直接は起動しません。実行するのは下の wrapper で、それを起動してよいのは
trusted command host だけです。host はこのファイルの wrapper を、記載された引数だけで実行してください。
未検証の path や同名ファイルへ置き換えて実行してはいけません。

次は no-clobber の呼び出し例です。下書き本文は shell argv に載せず、shell history や run-state にも
保存しません。secure run-stateには hashだけが残ります。stdout の最終完成稿は同じ private directory の一時ファイルへ受け、
成功後にhard-linkを使って未作成のoutput名を原子的に確保します。
wrapper内部では `rig-wb run japanese-writing-revision` に検証済みsource FDをstdinとして直接渡します。
Claude Code session 内でも意図した headless Claude を起動するため、wrapper は
`--allow-headless-in-cc` を明示します。これは外側の session とは別 subprocess を起動し、利用状況に
よっては別の課金枠を消費し得るため、実行する trusted command host はこの選択を利用者へ示してください。

```sh
set -eu
umask 077
[ "$#" -ge 2 ] || { printf '%s\n' '[BLOCKED] draft and output paths are required' >&2; exit 2; }
draft_path=$1
output_path=$2
shift 2
review_category=
material_profile=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --review-category)
      [ "$#" -ge 2 ] && [ -z "$review_category" ] || {
        printf '%s\n' '[BLOCKED] --review-category must appear once with a value' >&2
        exit 2
      }
      review_category=$2
      shift 2
      ;;
    --material-profile)
      [ "$#" -ge 2 ] && [ -z "$material_profile" ] || {
        printf '%s\n' '[BLOCKED] --material-profile must appear once with a value' >&2
        exit 2
      }
      material_profile=$2
      shift 2
      ;;
    *)
      printf '%s\n' "[BLOCKED] unknown option: $1" >&2
      exit 2
      ;;
  esac
done
case "$review_category" in
  general|incident_report|support_reply) ;;
  *) printf '%s\n' '[BLOCKED] --review-category requires general|incident_report|support_reply' >&2; exit 2 ;;
esac
case "$material_profile" in
  none|technical|conversation) ;;
  *) printf '%s\n' '[BLOCKED] --material-profile requires none|technical|conversation' >&2; exit 2 ;;
esac
python3 - "$draft_path" "$output_path" "$review_category" "$material_profile" \
  "$PWD/.rig/provider-pins.json" <<'PY'
import os
import pathlib
import secrets
import stat
import subprocess
import sys

DIR_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def absolute_canonical(raw, label):
    if not os.path.isabs(raw) or raw != os.path.normpath(raw):
        raise ValueError(f"{label} must be an absolute canonical path")
    path = pathlib.Path(raw)
    if path.name in ("", ".", ".."):
        raise ValueError(f"{label} must name a file")
    return path


def open_private_directory(path):
    descriptor = os.open(path.anchor, DIR_FLAGS)
    try:
        for component in path.parts[1:]:
            if component in ("", ".", ".."):
                raise ValueError("private directory path is not canonical")
            child = os.open(component, DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise OSError("private directory must be caller-owned mode 0700")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def verify_private_file(info, label):
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise OSError(
            f"{label} must be a caller-owned regular file mode 0600 with one link"
        )


def run():
    source = absolute_canonical(sys.argv[1], "draft")
    output = absolute_canonical(sys.argv[2], "output")
    if source == output:
        raise ValueError("draft and output paths must differ")
    category, material_profile, pin_config = sys.argv[3:6]
    source_directory = output_directory = source_fd = temporary_fd = -1
    temporary_name = None
    try:
        source_directory = open_private_directory(source.parent)
        source_fd = os.open(source.name, FILE_FLAGS, dir_fd=source_directory)
        source_info = os.fstat(source_fd)
        verify_private_file(source_info, "draft")
        path_info = os.stat(
            source.name, dir_fd=source_directory, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(path_info.st_mode)
            or (path_info.st_dev, path_info.st_ino)
            != (source_info.st_dev, source_info.st_ino)
        ):
            raise OSError("draft path no longer names the verified file descriptor")

        output_directory = open_private_directory(output.parent)
        try:
            os.stat(output.name, dir_fd=output_directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError("output already exists")
        temporary_name = f".{output.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=output_directory,
        )
        os.fchmod(temporary_fd, 0o600)
        verify_private_file(os.fstat(temporary_fd), "temporary output")

        completed = subprocess.run(
            [
                "rig-wb", "run", "japanese-writing-revision",
                "--provider", "claude",
                "--verifier-provider", "codex",
                "--secure-provider-config", pin_config,
                "--review-category", category,
                "--material-profile", material_profile,
                "--goal-stdin",
                "--allow-headless-in-cc",
            ],
            stdin=source_fd,
            stdout=temporary_fd,
            shell=False,
            check=False,
        )
        if completed.returncode != 0:
            return completed.returncode
        os.fsync(temporary_fd)
        if os.fstat(temporary_fd).st_size == 0:
            raise OSError("approved output is empty")
        os.link(
            temporary_name, output.name,
            src_dir_fd=output_directory, dst_dir_fd=output_directory,
            follow_symlinks=False,
        )
        os.unlink(temporary_name, dir_fd=output_directory)
        temporary_name = None
        os.fsync(output_directory)
        return 0
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if temporary_name is not None and output_directory >= 0:
            try:
                os.unlink(temporary_name, dir_fd=output_directory)
            except FileNotFoundError:
                pass
        for descriptor in (source_fd, source_directory, output_directory):
            if descriptor >= 0:
                os.close(descriptor)


try:
    raise SystemExit(run())
except (OSError, ValueError) as error:
    print(f"[BLOCKED] secure draft revision: {error}", file=sys.stderr)
    raise SystemExit(2)
PY
```

この例は draft をprivate dirfdから `O_NOFOLLOW` で一度だけopenし、そのFDのowner・regular type・mode
`0600`・link count 1と、同じdirfd上のpathのdevice/inode bindingをprovider call前に検証します。その後の
path交換にかかわらず同じ検証済みFDだけをstdinへ渡します。output directoryもowner所有・mode `0700` の
canonical pathに限定します。条件を満たすように変更する責任は呼び出し側にあり、このcommandはsourceの
modeも内容も変更しません。hard-linkによる公開は既存 output に対して失敗するためsourceも既存成果物も
上書きしません。同一filesystemのprivate directoryを使ってください。失敗・
`REVISE`継続・未検証時はwrapperが一時ファイルを削除し、sourceは変更しません。診断はstderr、stdoutは
承認済みの最終成果物だけです。

metadataへ渡す引数では、二つのpathと二つのselectorをすべて明示します。

下書きには、残すべき宛先、掲載先、固有名詞、数値、日時、状態、条件、否定を含めてください。
秘密値が含まれていても再表示せず `[REDACTED]` に置換します。入力にない原因、意図、期限、約束は
追加しません。reviewerは既存strict JSON contractを使い、修正は最大一回です。

## モード

`--mode` は文体のモードです。`plain`（既定）、`talk`、`dialogue`、`onomatopoeia`、`emoji`
から選び、カンマで複数指定できます（例: `--mode talk,emoji`）。本文からは推測しません。
モードは表現の手段を足すだけで、事実の扱い、秘密情報、完成稿の形についての制約は
一つも解除しません。各モードで許可されること、許可されても避けることは
`japanese-writing-modes` にあります。`incident_report` と `support_reply` では、
`emoji` を指定されても絵文字を使いません。

```text
rig-wb run japanese-writing-revision --mode talk --goal-stdin < draft.txt
rig-wb run japanese-writing-revision --mode talk,emoji --goal-stdin < draft.txt
```
