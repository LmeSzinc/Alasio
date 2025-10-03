<script lang="ts">
  import * as Select from "$lib/components/ui/select/index.js";
  import { useTopic } from "$lib/ws";

  type Props = {
    mod_name: string;
    selectFirst?: boolean;
    disabled?: boolean;
    placeholder?: string;
  };

  let {
    mod_name = $bindable(),
    selectFirst = false,
    disabled = false,
    placeholder = "Select a module",
  }: Props = $props();

  type ModOption = {
    value: string;
    label: string;
  };

  const topicMod = useTopic<ModOption[]>("ModList");
  const availableMods = $derived(topicMod.data || []);

  const triggerContent = $derived(availableMods.find((m) => m.value === mod_name)?.label ?? placeholder);

  // Auto-select first mod if selectFirst is true and mod_name is empty
  $effect(() => {
    if (selectFirst && !mod_name && availableMods.length > 0) {
      mod_name = availableMods[0].value;
    }
  });
</script>

<Select.Root type="single" bind:value={mod_name} {disabled}>
  <Select.Trigger class="w-full">
    {triggerContent}
  </Select.Trigger>
  <Select.Content>
    <Select.Group>
      {#if availableMods.length > 0}
        {#each availableMods as modOption (modOption.value)}
          <Select.Item value={modOption.value} label={modOption.label}>
            {modOption.label}
          </Select.Item>
        {/each}
      {:else}
        <Select.Label>No available mod</Select.Label>
      {/if}
    </Select.Group>
  </Select.Content>
</Select.Root>
