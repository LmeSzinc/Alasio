<script lang="ts">
  import * as Select from "$lib/components/ui/select";
  import { t } from "$lib/i18n";
  import { ANY_ITEM, type RegexLevel, type RegexValues, regexCandidates } from "./filterOrder";

  /**
   * The inline selects of one dt="filter-regex" row: one select per level.
   *
   * Every level offers "any" (the empty value) first, then the candidates of the
   * level for the current selection. The value the row already holds is offered
   * even when it is not a candidate anymore: a predicate that is not in the
   * table only never matches, it is not an error (the backend says the same).
   */
  type Props = {
    /** Levels of the arg, in the declared order */
    levels: RegexLevel[];
    /** Value of every level, an unset level is the empty string */
    values: RegexValues;
    /** Label of a level value, translated through option_i18n by the caller */
    label: (value: string) => string;
    /** Called with the level name and its new value, empty for "any" */
    onchange: (name: string, value: string) => void;
  };
  let { levels, values, label, onchange }: Props = $props();
</script>

{#each levels as level (level.name)}
  {@const current = values[level.name] ?? ""}
  {@const candidates = regexCandidates(level, values)}
  {@const options = current !== "" && !candidates.includes(current) ? [current, ...candidates] : candidates}
  <Select.Root
    type="single"
    value={current === "" ? ANY_ITEM : current}
    onValueChange={(next) => next && onchange(level.name, next === ANY_ITEM ? "" : next)}
  >
    <Select.Trigger
      size="sm"
      class="h-6! min-w-0 flex-1 gap-1 border-0 bg-accent px-1.5 py-0 text-xs shadow-none"
      aria-label={level.name}
    >
      <span class="min-w-0 flex-1 truncate text-left" class:text-muted-foreground={current === ""}>
        {current === "" ? t.Input.FilterOrderAny() : label(current)}
      </span>
    </Select.Trigger>
    <Select.Content>
      <Select.Group>
        <Select.Item value={ANY_ITEM} label={t.Input.FilterOrderAny()}>
          {t.Input.FilterOrderAny()}
        </Select.Item>
        {#each options as option (option)}
          {@const optionLabel = label(option)}
          <Select.Item value={option} label={optionLabel}>{optionLabel}</Select.Item>
        {/each}
      </Select.Group>
    </Select.Content>
  </Select.Root>
{/each}
