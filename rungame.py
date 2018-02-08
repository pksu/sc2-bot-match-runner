#!python3
# usage: ./rungame.py [args] repo1 repo2

PROCESS_POLL_INTERVAL = 3 # seconds

from pathlib import Path
import random
import time
import json
import argparse
import subprocess as sp
import shutil

from repocache import RepoCache
import read_replay

def copy_contents(from_directory: Path, to_directory: Path):
    for path in from_directory.iterdir():
        fn = shutil.copy if path.is_file() else shutil.copytree
        fn(path, to_directory)

def prepend_all(prefix, container):
    return [r for item in container for r in [prefix, item]]

def main():
    parser = argparse.ArgumentParser(description="Process some integers.")
    parser.add_argument("--realtime", action="store_true", help="run in realtime mode")
    parser.add_argument("map_name", type=str, help="map name")
    parser.add_argument("repo", type=str, nargs="+", help="a list of repositories")
    args = parser.parse_args()

    if len(args.repo) != 2:
        exit("There must be exactly two repositories.")

    for repo in args.repo:
        if not repo.startswith("https://"):
            print(f"Please use https url to repo, and not {repo}")
            exit(2)

    # TODO: args.realtime

    # Create empty containers/ directory (removes the old one)
    containers = Path("containers")
    if containers.exists():
        shutil.rmtree(containers)
    containers.mkdir()

    # Create empty results/ directory (removes the old one)
    results = Path("results")
    if results.exists():
        shutil.rmtree(results)
    results.mkdir()

    # Create empty results/ directory (removes the old one)
    replays = Path("replays")
    if replays.exists():
        shutil.rmtree(replays)
    replays.mkdir()

    start_all = time.time()
    start = start_all

    repocache = RepoCache()

    MATCHES = [
        [args.repo[0], args.repo[1]],
        [args.repo[0], args.repo[1]]
    ]

    # Clone repos and create match folders
    print("Fetching repositiories...")
    for i_match, repos in enumerate(MATCHES):
        container = containers / f"match{i_match}"
        for i, repo in enumerate(repos):
            repo_path = repocache.get(repo)
            shutil.copytree(repo_path, container / f"repo{i}")

    print(f"Ok ({time.time() - start:.2f}s)")

    # Collect bot info
    botinfo_by_match = []
    for i_match, repos in enumerate(MATCHES):
        botinfo_by_match.append([])
        container = containers / f"match{i_match}"
        for i, repo in enumerate(repos):
            botinfo_file = container / f"repo{i}" / "botinfo.json"

            if not botinfo_file.exists():
                print(f"File botinfo.json is missing for repo{i}")
                exit(3)

            with open(botinfo_file) as f:
                botinfo = json.load(f)

            REQUIRED_KEYS = {"race": str, "name": str}
            for k, t in REQUIRED_KEYS.items():
                if k not in botinfo or not isinstance(botinfo[k], t):
                    print(f"Invalid botinfo.json for repo{i}:")
                    print(f"Key '{k}' missing, or type is not {t !r}")
                    exit(3)

            botinfo_by_match[-1].append(botinfo)

    races_by_match = [[b["race"] for b in info] for info in botinfo_by_match]

    start = time.time()
    print("Starting games...")
    for i_match, repos in enumerate(MATCHES):
        container = containers / f"match{i_match}"

        # stdout_log = open(container / "stdout.log", "a")
        # stderr_log = open(container / "stderr.log", "a")
        # stdout=stdout_log, stderr=stderr_log, cwd=container

        copy_contents(Path("template_container"), container)
        shutil.copytree(Path("/Users/dento/Desktop/python-sc2"), container / "python-sc2")
        # HACK: using mount would be better, but it doesn't work is so slow that
        #       it just seems to block forever.
        # shutil.copytree(Path("StarCraftII"), container / "StarCraftII")

        image_name =  f"sc2_repo{0}_vs_repo{1}_image"
        process_name =  f"sc2_match{i_match}"

        sp.run(["docker", "rm", process_name], cwd=container, check=False)
        sp.run(["docker", "build", "-t", image_name, "."], cwd=container, check=True)
        sp.run([
            "docker", "run", "-d",
            "--env", f"sc2_match_id={i_match}",
            "--env", f"sc2_map_name={args.map_name}",
            "--env", f"sc2_races={','.join(races_by_match[i_match])}",
            "--mount", ",".join(map("=".join, {
                "type": "bind",
                "source": str(Path("StarCraftII").resolve(strict=True)),
                "destination": "/StarCraftII",
                "readonly": "true",
                "consistency": "cached"
            }.items())),
            "--mount", ",".join(map("=".join, {
                "type": "bind",
                "source": str(Path("replays").resolve(strict=True)),
                "destination": "/replays",
                "readonly": "false",
                "consistency": "consistent"
            }.items())),
            "--name", process_name,
            image_name
        ], cwd=container, check=True)

    print(f"Ok ({time.time() - start:.2f}s)")

    start = time.time()
    print("Running game...")
    while True:
        docker_process_ids = sp.check_output([
            "docker", "ps", "-q",
            "--filter", f"volume={Path('StarCraftII').resolve(strict=True)}"
        ]).split()

        if len(docker_process_ids) == 0:
            break

        time.sleep(PROCESS_POLL_INTERVAL)

    print(f"Ok ({time.time() - start:.2f}s)")

    start = time.time()
    print("Collecting results...")
    winners = []
    for i_match, repos in enumerate(MATCHES):
        winner_info = None

        for i, repo in enumerate(repos):
            try:
                replay_winners = read_replay.winners(replays / f"{i_match}_{i}.SC2Replay")
            except FileNotFoundError:
                print(f"Process match{i_match}:repo{i} didn't record a replay")
                continue

            if winner_info is None:
                winner_info = replay_winners
            elif winner_info != replay_winners:
                print(f"Conflicting winner information (match{i_match}:repo{i})")
                print(f"({replay_winners !r})")
                print(f"({winner_info !r})")

        if winner_info is None:
            print("No replays were recorded by either client")
            exit(1)

        # TODO: Assumes player_id == repo_index
        # Might be possible to at least try to verify this assumption
        for player_id, victory in winner_info.items():
            if victory:
                winners.append(player_id)
                break
        else: # Tie
            winners.append(None)

    result_dir = Path("results")
    result_dir.mkdir(parents=True, exist_ok=True)

    result_data = [
        {
            "winner": winner_id,
            "repositories": MATCHES[i_match]
        }
        for i_match, winner_id in enumerate(winners)
    ]

    with open(result_dir / "results.json", "w") as f:
        json.dump(result_data, f)

    print(f"Ok ({time.time() - start:.2f}s)")

    print(f"Completed (total {time.time() - start_all:.2f}s)")


if __name__ == "__main__":
    main()