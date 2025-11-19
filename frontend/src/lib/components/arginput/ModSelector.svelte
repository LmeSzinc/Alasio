<script lang="ts">
  import * as Select from "$lib/components/ui/select/index.js";
  import { t } from "$lib/i18n";
  import { useTopic } from "$lib/ws";

  type Props = {
    mod_name: string;
    disabled?: boolean;
    class?: string;
    // Optional callback when value changed
    handleEdit?: (value: string) => void;
  };

  let { mod_name = $bindable(), disabled = false, class: className, handleEdit }: Props = $props();

  type ModOption = {
    value: string;
    label: string;
  };

  const topicMod = useTopic<ModOption[]>("ModList");
  const availableMods = $derived(topicMod.data || []);
  const selectedMod = $derived(availableMods.find((m) => m.value === mod_name));
  const triggerContent = $derived(selectedMod?.label ?? t.Mod.SelectMod());

  // Auto select first mod if there is only one
  $effect(() => {
    if (selectedMod) return;
    if (availableMods.length > 0) {
      mod_name = availableMods[0].value;
    }
  });

  function onValueChange(value: string | undefined) {
    if (value && handleEdit) {
      handleEdit(value);
    }
  }
</script>

<div class={className}>
  <Select.Root type="single" bind:value={mod_name} {disabled} {onValueChange}>
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
          <Select.Label>{t.Mod.NoAvailableMod()}</Select.Label>
        {/if}
      </Select.Group>
    </Select.Content>
  </Select.Root>
</div>
