<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import ModManager, { type ModHistoryData, type ModOption } from "../../mod/ModManager.svelte";

  const mockMods: ModOption[] = [
    { value: "alasio", label: "alasio" },
    { value: "example_mod", label: "example_mod" },
    { value: "src", label: "src" },
  ];

  function commit(
    version: string,
    author: string,
    time: number,
    title: string,
    detail = "",
  ): NonNullable<ModHistoryData[string]["data"]>[number] {
    return { version, author, time, title, detail };
  }

  const mockHistory: ModHistoryData = {
    // 5 commits: preview shows 3, expand-all shows 5, 2 commits have detail
    alasio: {
      data: [
        commit(
          "0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c8",
          "LmeSzinc",
          1786730962,
          "Add: history.pack in full pack",
          "Pack the release history into a normal file inside the full pack.\n\nThe history is decoded from .pack/history.pack on the client side\nto show the release history of a mod.",
        ),
        commit(
          "ab41c81d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b",
          "LmeSzinc",
          1786730000,
          "Add: history cli to build pack to self",
        ),
        commit("d3599d6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e", "LmeSzinc", 1786729000, "Dep: add /.pack to gitignore"),
        commit(
          "0489128a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5",
          "LmeSzinc",
          1786728000,
          "Add: pack commit history",
          "Encode the latest commits into a msgpack array, one item per commit.",
        ),
        commit("5296f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6", "LmeSzinc", 1786727000, "Dep: add zstandard"),
      ],
    },
    // error state
    example_mod: {
      data: [],
      error: "FileNotFoundError: [Errno 2] No such file or directory: 'ExampleMod/.pack/history.pack'",
    },
    // empty state
    src: {
      data: [],
    },
  };
</script>

<div class="container mx-auto flex flex-col gap-8 overflow-auto p-6 pb-20">
  <h1 class="text-3xl font-bold">Mod Manager Test</h1>
  <p class="text-muted-foreground">
    ModManager rendering with mock data: commit preview (3) and expand-all, detail expand button, error state, and empty
    history state.
  </p>

  <ModManager mods={mockMods} history={mockHistory} />
</div>
