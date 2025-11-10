<script lang="ts">
  import * as Select from "$lib/components/ui/select/index.js";
  import { useTopic } from "$lib/ws";

  type Props = {
    mod_name: string;
    disabled?: boolean;
    placeholder?: string;
    class?: string;
    // Optional callback when value changed
    handleEdit?: (value: string) => void;
  };

  let {
    mod_name = $bindable(),
    disabled = false,
    placeholder = "Select a module",
    class: className,
    handleEdit,
  }: Props = $props();

  type ModOption = {
    value: string;
    label: string;
  };

  const topicMod = useTopic<ModOption[]>("ModList");
  const availableMods = $derived(topicMod.data || []);

  const triggerContent = $derived(availableMods.find((m) => m.value === mod_name)?.label ?? placeholder);

  function onValueChange(value: string | undefined) {
    if (value && handleEdit) {
      handleEdit(value);
    }
  }
</script>

<div class={className}>
  <Select.Root type="single" value={mod_name} {disabled} {onValueChange}>
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
</div>
