"""Translator-facing setup, validation, build, install, and restore commands."""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

if os.environ.get("MARRY_UTF8_CONSOLE") == "1":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent)
SOURCE_CSV = ROOT / "translation" / "translation.csv"
SAMPLE_CSV = ROOT / "translation" / "sample_ja.csv"
TEMPLATE_CSV = ROOT / "translation" / "template.csv"
BACKUP = ROOT / "game_backup" / "data.win.orig"
BUILD = ROOT / "build" / "data.win"
STATE = ROOT / "workspace" / "state.json"
EXPECTED_SHA256 = "aac58daa7ab157763a92c63ce3bd19781ffef5cd0bace7732965dc17f7e1c182"
PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z_0-9]*\}")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(value):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def game_file(game):
    path = Path(game).expanduser().resolve()
    if path.is_file() and path.name.lower() == "data.win":
        return path
    return path / "data.win"


def steam_root():
    if os.name != "nt":
        return None
    import winreg
    for key_name in (r"Software\Valve\Steam", r"Software\WOW6432Node\Valve\Steam"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as key:
                return Path(winreg.QueryValueEx(key, "SteamPath")[0])
        except OSError:
            continue
    return None


def discover_game_paths(root=None):
    """Steamのライブラリ一覧とアプリのmanifestからインストール先を探す。"""
    root = root or steam_root()
    if root is None:
        return []
    libraries = [Path(root)]
    listing = Path(root) / "steamapps" / "libraryfolders.vdf"
    if listing.is_file():
        content = listing.read_text(encoding="utf-8-sig", errors="replace")
        for value in re.findall(r'"path"\s+"((?:\\.|[^"\\])*)"', content, re.I):
            libraries.append(Path(value.replace("\\\\", "\\")))
    found = []
    for library in libraries:
        steamapps = library / "steamapps"
        manifest = steamapps / "appmanifest_3143760.acf"
        if manifest.is_file():
            content = manifest.read_text(encoding="utf-8-sig", errors="replace")
            match = re.search(r'"installdir"\s+"([^"]+)"', content, re.I)
            names = [match.group(1)] if match else []
        else:
            names = []
        names.append("Marry a Deep One")
        for name in names:
            if Path(name).name != name:
                continue
            candidate = steamapps / "common" / name
            if (candidate / "data.win").is_file() and (candidate / "Marry a Deep One.exe").is_file():
                resolved = candidate.resolve()
                if resolved not in found:
                    found.append(resolved)
    return found


def locate(args):
    paths = discover_game_paths()
    for path in paths:
        print(path)
    if not paths:
        print("Steamライブラリ内にゲームが見つかりませんでした")


def current_game(args):
    chosen = args.game or state().get("game")
    if not chosen:
        raise ValueError("ゲームフォルダーが未設定です。最初に init --game <ゲームフォルダー> を実行してください")
    target = game_file(chosen)
    if not target.is_file():
        raise ValueError(f"data.win が見つかりません: {target}")
    return target


def ensure_backup():
    if not BACKUP.is_file() or sha256(BACKUP) != EXPECTED_SHA256:
        raise ValueError("原本のバックアップが見つからないか、対応版と一致しません。init を実行してください")


def init(args):
    target = game_file(args.game)
    if not target.is_file():
        raise ValueError(f"data.win が見つかりません: {target}")
    if BACKUP.exists():
        ensure_backup()
    else:
        if sha256(target) != EXPECTED_SHA256:
            raise ValueError("ゲームのdata.winが対応版の原本ではありません。Steamでファイルの整合性を確認してから、もう一度実行してください")
        BACKUP.parent.mkdir(exist_ok=True)
        shutil.copy2(target, BACKUP)
        ensure_backup()
    value = state()
    value["game"] = str(target.parent)
    save_state(value)
    print(f"準備完了: {target.parent}\n原本バックアップ: {BACKUP}")


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSVの見出しがありません: {path}")
        return reader.fieldnames, list(reader)


def check(args):
    from build_jp_font import FALLBACK_TTF, FONTS, JP_FONT_FOR, jp_charset
    from jp_text import missing_glyphs

    fields, base = read_rows(TEMPLATE_CSV)
    edited_path = Path(args.csv).resolve()
    edited_fields, edited = read_rows(edited_path)
    errors = []
    if fields != edited_fields:
        errors.append("列名または列順が原本CSVと異なります")
    ids = [r.get("id", "") for r in edited]
    if len(ids) != len(set(ids)):
        errors.append("id が重複しています")
    baseline = {r["id"]: r for r in base}
    if set(ids) != set(baseline):
        errors.append(f"行が不足／追加されています（不足 {len(set(baseline)-set(ids))}、追加 {len(set(ids)-set(baseline))}）")
    allowed = set(jp_charset(FONTS / JP_FONT_FOR["font_m"], FALLBACK_TTF))
    translated = 0
    for row in edited:
        ref = baseline.get(row.get("id", ""))
        if not ref:
            continue
        row_id = row["id"]
        for field in fields:
            if field != "ja" and row.get(field, "") != ref.get(field, ""):
                errors.append(f"[{row_id}] ja 以外の列が変更されています: {field}")
                break
        ja = row.get("ja", "")
        if not ja.strip():
            continue
        translated += 1
        if ref["translate"] == "no":
            errors.append(f"[{row_id}] translate=no の内部文字列に訳文があります")
        if Counter(PLACEHOLDER.findall(ja)) != Counter(PLACEHOLDER.findall(ref["en"])):
            errors.append(f"[{row_id}] プレースホルダーの種類または個数が違います")
        source_breaks = ref["en"].count("#")
        target_breaks = ja.count("#")
        if target_breaks < source_breaks or (ref["concat"] != "yes" and target_breaks != source_breaks):
            errors.append(f"[{row_id}] # の個数が違います")
        if "\n" in ja or "\r" in ja:
            errors.append(f"[{row_id}] 訳文に改行があります")
        missing = missing_glyphs(ja, allowed)
        if missing:
            errors.append(f"[{row_id}] フォントにない文字: {''.join(missing)}")
    print(f"翻訳済み {translated}/{sum(r['translate']=='yes' for r in base)} 件")
    for line in errors[:30]:
        print("エラー:", line)
    if len(errors) > 30:
        print(f"ほかにエラー {len(errors)-30} 件")
    if errors:
        raise ValueError(f"CSV検査で {len(errors)} 件のエラーがありました")
    print("CSV検査OK")


def build(args):
    ensure_backup()
    check(args)
    from build_jp_font import main as build_main
    build_main(translate=str(Path(args.csv).resolve()))
    print(f"ビルド完了: {BUILD}\nゲームへ反映するには install を実行してください")


def game_running(target):
    if os.name != "nt":
        return False
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Marry a Deep One.exe", "/FO", "CSV"],
                            capture_output=True, text=True, errors="replace", check=True)
    return '"Marry a Deep One.exe"' in result.stdout


def replace_game(target, source):
    temp = target.with_name("data.win.mod-tmp")
    shutil.copy2(source, temp)
    if sha256(temp) != sha256(source):
        temp.unlink()
        raise ValueError("コピー後のハッシュが一致しません")
    os.replace(temp, target)


def install(args):
    ensure_backup()
    target = current_game(args)
    if not BUILD.is_file():
        raise ValueError("ビルド済みの data.win がありません。build を実行してください")
    if game_running(target):
        raise ValueError("ゲームが起動中です。終了してから再実行してください")
    value = state()
    current = sha256(target)
    if current not in (EXPECTED_SHA256, value.get("installed_sha256")):
        raise ValueError("ゲームのdata.winが対応版または前回反映したファイルと一致しません。上書きはしていません")
    replace_game(target, BUILD)
    value["installed_sha256"] = sha256(BUILD)
    save_state(value)
    print(f"反映完了: {target}\nゲームを起動して表示を確認してください")


def restore(args):
    ensure_backup()
    target = current_game(args)
    if game_running(target):
        raise ValueError("ゲームが起動中です。終了してから再実行してください")
    current = sha256(target)
    if current not in (EXPECTED_SHA256, state().get("installed_sha256")):
        raise ValueError("ゲームのdata.winが対応版または前回反映したファイルと一致しません。上書きはしていません")
    replace_game(target, BACKUP)
    print(f"原本に戻しました: {target}")


def menu():
    if not state().get("game"):
        paths = discover_game_paths()
        if len(paths) == 1:
            path = str(paths[0])
            print(f"Steamライブラリでゲームが見つかりました: {path}")
        elif len(paths) > 1:
            print("ゲームが複数見つかりました:")
            for index, found in enumerate(paths, 1):
                print(f"{index}: {found}")
            answer = input("使う番号（またはゲームフォルダーのパス）: ").strip().strip('"')
            path = str(paths[int(answer) - 1]) if answer.isdigit() and 1 <= int(answer) <= len(paths) else answer
        else:
            path = input("Steamから見つかりませんでした。ゲームフォルダーを入力: ").strip().strip('"')
        init(argparse.Namespace(game=path))
    while True:
        print("\n1 サンプル訳を反映  2 作業用CSVを反映  3 作業用CSVを検査  4 原本に戻す  5 終了")
        choice = input("番号: ").strip()
        if choice == "5":
            return
        if choice not in ("1", "2", "3", "4"):
            continue
        try:
            if choice in ("1", "2"):
                if game_running(current_game(argparse.Namespace(game=None))):
                    raise ValueError("ゲームを終了してから反映してください")
                selected = SAMPLE_CSV if choice == "1" else SOURCE_CSV
                print("日本語化データを作成中です。しばらくお待ちください。")
                details = io.StringIO()
                try:
                    with redirect_stdout(details):
                        build(argparse.Namespace(csv=str(selected)))
                        install(argparse.Namespace(game=None))
                except (ValueError, OSError, AssertionError):
                    print(details.getvalue(), end="")
                    raise
                print("反映完了。ゲームを起動してください。")
            elif choice == "3":
                check(argparse.Namespace(csv=str(SOURCE_CSV)))
            else:
                restore(argparse.Namespace(game=None))
        except (ValueError, OSError, AssertionError) as exc:
            print("失敗:", exc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init_parser = commands.add_parser("init", help="ゲームの原本を取り込んでバックアップ")
    init_parser.add_argument("--game", required=True, help="ゲームフォルダーまたはdata.win")
    for name in ("check", "build"):
        sub = commands.add_parser(name)
        sub.add_argument("--csv", default=str(SOURCE_CSV))
    for name in ("install", "restore"):
        sub = commands.add_parser(name)
        sub.add_argument("--game", help="初回initで保存した場所を使う場合は省略")
    commands.add_parser("menu")
    commands.add_parser("locate", help="Steamのライブラリからゲームを探す")
    args = parser.parse_args()
    try:
        if args.command == "menu":
            menu()
        else:
            globals()[args.command](args)
    except (ValueError, OSError, AssertionError) as exc:
        parser.exit(1, f"失敗: {exc}\n")


if __name__ == "__main__":
    main()
