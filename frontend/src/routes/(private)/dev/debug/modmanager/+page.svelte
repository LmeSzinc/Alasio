<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import type { UPDATE_STATE, UpdateInfoLike, UpdateTopicLike } from "$lib/components/aside/types";
  import { type HistoryItem, type ModHistoryData, type ModOption } from "$lib/components/update/utils";
  import ModManager from "../../updater/ModManager.svelte";

  // ModManager (the card list of the mod page) reads the ModList / ModHistory /
  // Update topics in production. Every scenario below overrides them with props,
  // so all states render deterministically without a live backend. Only the
  // buttons keep the rpc of their topic: a click really checks / updates /
  // cancels on the backend behind this page.

  type Scenario = {
    label: string;
    note: string;
    mods: ModOption[];
    // always passed, even when empty: a missing prop falls back to the topic
    history: ModHistoryData;
    update: UpdateTopicLike;
  };

  function mod(value: string, label = value): ModOption {
    return { value, label };
  }

  function commit(version: string, author: string, time: number, title: string, detail = ""): HistoryItem {
    return { version, author, time, title, detail };
  }

  // unix seconds of 2026-08-17, the base of the mock commit times (a commit
  // time is rendered in the local time of the viewer)
  const BASE = 1786727000;
  const HOUR = 3600;
  // the moment the page is rendered: the checked times of the mock data are
  // relative to it ("checked 5m ago")
  const NOW = Math.floor(Date.now() / 1000);

  // 40 hex characters so a version is shown shortened to 8, like the commit
  // sha1 of a real pack. Versions inside a mod differ: they are the #each keys
  // of the commit list.
  const SHA = {
    a: "0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c8",
    b: "ab41c81d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b",
    c: "d3599d6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e",
    d: "5296f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6",
    e: "3f8e1b6a5c4d2e0f9a8b7c6d5e4f3a2b1c0d9e8f",
  } as const;

  const TITLES = [
    "Add: history.pack in full pack",
    "Fix: pack the untouched files of a partial update",
    "Pref: faster fileinfo building",
    "Dep: add zstandard",
    "Opt: fewer requests during a check",
  ];

  /** The pack of a mod: `count` commits, one every 6 hours, `detailOn` expanded. */
  function pack(count: number, detailOn: number[] = []): ModHistoryData[string] {
    const versions = Object.values(SHA);
    const data = Array.from({ length: count }, (_, index) =>
      commit(
        versions[index % versions.length],
        "LmeSzinc",
        BASE - index * 6 * HOUR,
        TITLES[index % TITLES.length],
        detailOn.includes(index)
          ? "Pack the release history into a normal file inside the full pack.\n\nThe history is decoded from .pack/history.pack on the client side to show the release history of a mod."
          : "",
      ),
    );
    return { data };
  }

  /** One entry of the Update topic, as the backend reports it. */
  function updateInfo(state: UPDATE_STATE, current = "", latest = "", error = "", checkedAt = NOW): UpdateInfoLike {
    return { state, current_version: current, latest_version: latest, checked_at: checkedAt, error };
  }

  // Every state of the Update topic with the versions and the error line a
  // backend reports in that state
  const updateStates: { state: UPDATE_STATE; current: string; latest: string; error?: string }[] = [
    { state: "no-supervisor", current: "0beb992", latest: "" },
    { state: "unmanaged", current: "0beb992", latest: "" },
    { state: "disabled", current: "0beb992", latest: "" },
    { state: "idle", current: "0beb992", latest: "" },
    { state: "checking", current: "0beb992", latest: "ab41c81" },
    { state: "uptodate", current: "ab41c81", latest: "ab41c81" },
    { state: "available", current: "0beb992", latest: "ab41c81" },
    { state: "queued", current: "0beb992", latest: "ab41c81" },
    { state: "stopping", current: "0beb992", latest: "ab41c81" },
    { state: "downloading", current: "0beb992", latest: "ab41c81" },
    { state: "updating", current: "0beb992", latest: "ab41c81" },
    { state: "restart-required", current: "ab41c81", latest: "ab41c81" },
    { state: "error", current: "0beb992", latest: "", error: "ConnectionError: the update source is unreachable" },
  ];

  // The list of the last scenario: enough names to scroll, mixed histories
  const manyMods = [
    "alasio",
    "ExampleMod",
    "StarRailCopilot",
    "AzurLaneAutoScript",
    "alasio_speedup",
    "module",
    "webapp",
    "frontend",
  ];

  // The checked time of the first mod of the "up to date" scenario moves every
  // 3 seconds: the pill flashes the change, the feedback a finished check gives
  const tickingUpdate = $state<UpdateTopicLike>({
    checked_just_now: updateInfo("uptodate", "ab41c81", "ab41c81", "", NOW - 20),
    checked_5m: updateInfo("uptodate", "ab41c81", "ab41c81", "", NOW - 5 * 60),
    checked_3h: updateInfo("uptodate", "ab41c81", "ab41c81", "", NOW - 3 * HOUR),
    checked_3d: updateInfo("uptodate", "ab41c81", "ab41c81", "", NOW - 3 * 86400),
    checked_long_ago: updateInfo("uptodate", "ab41c81", "ab41c81", "", NOW - 120 * 86400),
  });

  $effect(() => {
    const timer = setInterval(() => {
      tickingUpdate.checked_just_now = updateInfo("uptodate", "ab41c81", "ab41c81", "", Math.floor(Date.now() / 1000));
    }, 3000);
    return () => clearInterval(timer);
  });

  const scenarios: Scenario[] = [
    {
      label: "No mod",
      note: "No mod installed: the dashed placeholder takes the place of the list (the update line is gone too).",
      mods: [],
      history: {},
      update: {},
    },
    {
      label: "History · preview and expand all",
      note: "5 commits preview the first 3 and carry the expand-all button, exactly 3 commits fit the preview without any button, 1 commit is a single row. No Update entry at all: the state pill reads like a mod waiting for its first check, the check button stays available.",
      mods: [mod("alasio"), mod("ExampleMod"), mod("small")],
      history: {
        alasio: pack(5, [0, 3]),
        ExampleMod: pack(3),
        small: pack(1),
      },
      update: {},
    },
    {
      label: "History · empty and error",
      note: "A mod without a single commit, and a mod whose history.pack failed to load (the message of the backend replaces the table). Both keep their pills: the version pill falls back to the version of the pack the update entry reports.",
      mods: [mod("empty_mod"), mod("broken_mod")],
      history: {
        empty_mod: { data: [] },
        broken_mod: {
          data: [],
          error: "FileNotFoundError: [Errno 2] No such file or directory: 'broken_mod/.pack/history.pack'",
        },
      },
      update: {
        empty_mod: updateInfo("uptodate", "0beb992", "0beb992"),
        broken_mod: updateInfo("idle", "ab41c81"),
      },
    },
    {
      label: "Local version · unavailable",
      note: "A mod whose pack reports no version at all: no commit history, and its update entry carries no current_version either. The version pill stays and reads 'Local version unavailable'.",
      mods: [mod("no_index_pack"), mod("unmanaged_mod")],
      history: {
        no_index_pack: { data: [] },
        unmanaged_mod: { data: [] },
      },
      update: {
        no_index_pack: updateInfo("idle"),
        unmanaged_mod: updateInfo("unmanaged"),
      },
    },
    {
      label: "Commit row · version, author and title",
      note: "Every row has the same height, with or without the expand button of a detail. The version column shows the first 8 characters of a sha1 and a tag name as it is; a long author or title truncates in its column and keeps the full text in the tooltip.",
      mods: [mod("commit_rows")],
      history: {
        commit_rows: {
          data: [
            commit(
              SHA.a,
              "LmeSzinc",
              BASE,
              "Add: history.pack in full pack",
              "The message body of the commit, opened by the plus button of the row.",
            ),
            commit("v1.2.3", "LmeSzinc", BASE - 6 * HOUR, "Dep: shadcn-svelte alert dialog"),
            commit(
              SHA.b,
              "a-very-long-github-name-that-should-truncate",
              BASE - 12 * HOUR,
              "Add: a commit title long enough to overflow its column and get the ellipsis of the truncate class",
            ),
          ],
        },
      },
      update: {},
    },
    {
      label: "Update · one mod per state",
      note: "Every state of the Update topic, one mod each: the pill of the state, the version pill of the pack (8 characters of a sha1) and the button of the check (disabled where the backend refuses a check, a state of a running flow, and 'checking' gets the cancel button instead).",
      mods: updateStates.map((entry) => mod(entry.state)),
      history: Object.fromEntries(
        updateStates.map((entry): [string, ModHistoryData[string]] => [entry.state, pack(2)]),
      ),
      update: Object.fromEntries(
        updateStates.map((entry): [string, UpdateInfoLike] => [
          entry.state,
          updateInfo(entry.state, entry.current, entry.latest, entry.error),
        ]),
      ),
    },
    {
      label: "Update · up to date",
      note: "The pill of a mod up to date carries the moment of the last finished check: 'Up to date - Just now' / '5m ago' / '3h ago' / '3d ago'. The first mod moves its checked time every 3 seconds: its state pill flashes the primary background for 1 second, the feedback a finished check gives (the values pushed when the page opens never flash).",
      mods: [mod("checked_just_now"), mod("checked_5m"), mod("checked_3h"), mod("checked_3d"), mod("checked_long_ago")],
      history: {
        checked_just_now: pack(2),
        checked_5m: pack(2),
        checked_3h: pack(2),
        checked_3d: pack(2),
        checked_long_ago: pack(2),
      },
      update: tickingUpdate,
    },
    {
      label: "Update · available",
      note: "The pill of an available update carries the version it brings, the line above counts the mods with an available update and enables the single update button: every one of them is applied at once, then the backend restarts and resumes the running configs. There is no confirmation dialog: the user reaches this page from the popup that announced the update, so the click here is already the second confirmation. Careful: the button calls the real update rpc of the backend behind this page.",
      mods: [mod("alasio"), mod("ExampleMod"), mod("small")],
      history: {
        alasio: pack(2),
        ExampleMod: pack(1),
        small: pack(1),
      },
      update: {
        alasio: updateInfo("available", "0beb992", "ab41c81"),
        ExampleMod: updateInfo("uptodate", "ab41c81", "ab41c81"),
      },
    },
    {
      label: "Update · cancellable flow",
      note: "The update line of a flow that may still be cancelled (a check of a mod, the stop of the workers, the download) shows the cancel button.",
      mods: [mod("alasio"), mod("ExampleMod")],
      history: {
        alasio: pack(2),
        ExampleMod: pack(1),
      },
      update: {
        alasio: updateInfo("downloading", "0beb992", "ab41c81"),
      },
    },
    {
      label: "Update · past the point of no return",
      note: "'updating' applies the update and cannot be cancelled any more: the line has no cancel button, the flow restarts the backend by itself when it finishes.",
      mods: [mod("alasio")],
      history: { alasio: pack(2) },
      update: {
        alasio: updateInfo("updating", "0beb992", "ab41c81"),
      },
    },
    {
      label: "Update · off",
      note: "Deploy.Update.AutoUpdate = false: no mod is ever checked (state 'disabled'), so the line says the updates are off instead of claiming every mod is up to date.",
      mods: [mod("alasio"), mod("ExampleMod")],
      history: { alasio: pack(2), ExampleMod: pack(1) },
      update: {
        alasio: updateInfo("disabled", "0beb992"),
      },
    },
    {
      label: "Update · no supervisor",
      note: "The backend runs without a live supervisor: no one could spawn the process an applied update needs, so nothing is checked either.",
      mods: [mod("alasio"), mod("ExampleMod")],
      history: { alasio: pack(2), ExampleMod: pack(1) },
      update: {
        alasio: updateInfo("no-supervisor", "0beb992"),
      },
    },
    {
      label: "Update · no update source",
      note: "The mod declares no update source (no mirror in its manifest): nothing to check, nothing to apply.",
      mods: [mod("alasio"), mod("ExampleMod")],
      history: { alasio: pack(2), ExampleMod: pack(1) },
      update: {
        alasio: updateInfo("unmanaged", "0beb992"),
      },
    },
    {
      label: "Update · check failed",
      note: "A check that failed: the flow marks the mod 'Update failed' and the message of the backend sits right below the title row, without a separator between the two. The message of a failed check of one mod does not touch the other mods.",
      mods: [mod("alasio"), mod("ExampleMod")],
      history: { alasio: pack(2), ExampleMod: pack(1) },
      update: {
        alasio: updateInfo("error", "", "", "TimeoutError: the update source did not answer in 30s"),
        ExampleMod: updateInfo("error", "ab41c81", "", "ValueError: malformed pack header"),
      },
    },
    {
      label: "Update · apply failed",
      note: "The apply step failed after the download (the files of an update are written locally, a failure in the middle leaves the pack on the old version): the flow marks the mod 'Update failed' and retrying starts the whole update again. 'Some files failed to update, retry the update' is the message the backend sends when only a part of the files could be replaced.",
      mods: [mod("alasio"), mod("example_mod")],
      history: { alasio: pack(2), example_mod: pack(1) },
      update: {
        alasio: updateInfo("error", "0beb992", "ab41c81", "Some files failed to update, retry the update"),
        example_mod: updateInfo(
          "error",
          "0beb992",
          "ab41c81",
          "PermissionError: [WinError 5] Access is denied: 'example_mod/base/config.py'",
        ),
      },
    },
    {
      label: "Long names",
      note: "A long mod label truncates in the card title while the pills keep their place next to it. A wheel mod shows its version like a tag, not a sha1.",
      mods: [mod("ExampleMod_with_a_very_long_repository_name"), mod("wheel_mod")],
      history: {
        ExampleMod_with_a_very_long_repository_name: pack(2),
        wheel_mod: { data: [commit("0.9.2", "LmeSzinc", BASE, "Rel: 0.9.2")] },
      },
      update: {
        wheel_mod: updateInfo("available", "0.9.1", "0.9.2"),
      },
    },
    {
      label: "Many mods",
      note: "A long mod list in mixed states: the page scrolls, the cards keep the same width.",
      mods: manyMods.map((name) => mod(name)),
      history: Object.fromEntries(
        manyMods.map((name, index): [string, ModHistoryData[string]] => [name, pack((index % 4) + 1)]),
      ),
      update: {
        alasio: updateInfo("uptodate", "ab41c81", "ab41c81"),
        ExampleMod: updateInfo("idle", "0beb992"),
        StarRailCopilot: updateInfo("available", "0beb992", "ab41c81"),
      },
    },
  ];
</script>

<div class="container mx-auto flex flex-col gap-8 p-4 pb-20">
  <div class="space-y-2">
    <h1 class="text-3xl font-bold">Mod Manager Debug Page</h1>
    <p class="text-muted-foreground text-sm">
      Every scenario of the ModManager card list with explicit mock data: the title row (the version pill of the pack,
      the pill of the update state, the check button), the message of a failure, the history of a mod (preview,
      expand-all, empty, error, long columns) and every state of the update flow (the single update entry). The buttons
      keep the real rpc of their topic.
    </p>
  </div>

  <section class="space-y-2">
    <div>
      <h2 class="text-lg font-semibold">Live</h2>
      <p class="text-muted-foreground text-xs">
        Subscribes the real ModList / ModHistory / Update topics: the mod list of the running backend, whatever state it
        is in. The buttons really check / update / cancel on the backend behind this page.
      </p>
    </div>
    <ModManager />
  </section>

  {#each scenarios as scenario (scenario.label)}
    <section class="space-y-2">
      <div>
        <h2 class="text-lg font-semibold">{scenario.label}</h2>
        <p class="text-muted-foreground text-xs">{scenario.note}</p>
      </div>
      <ModManager mods={scenario.mods} history={scenario.history} update={scenario.update} />
    </section>
  {/each}
</div>
