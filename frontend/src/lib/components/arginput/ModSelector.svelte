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
  const availableMods = $derived(Array.isArray(topicMod.data) ? topicMod.data : []);
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
    <Select.Trigger class="w-full overflow-hidden">
      <!--
        Hard truncation, no ellipsis: the trigger is now narrow enough that a
        long mod name has to be cut. `truncate` would add a `…`, and the
        `data-slot="select-value"` marker would apply the trigger's
        `line-clamp-1` (which also ellipsizes), so this span carries neither.
      -->
      <span class="min-w-0 overflow-hidden whitespace-nowrap">{triggerContent}</span>
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
