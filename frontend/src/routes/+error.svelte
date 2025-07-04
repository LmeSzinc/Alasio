<!-- src/routes/+error.svelte -->
<script>
  import { page } from "$app/stores";

  // 1. Get the props. On a direct load of 404.html, these might be undefined.
  let { status, error } = $props();

  // 2. Create derived values. These will use the props if they exist,
  //    otherwise, they will reactively fall back to the $page store.
  //    The SvelteKit router in the browser will update the $page store
  //    once it initializes, populating the values.
  const displayStatus = $derived(status ?? $page.status);
  const displayError = $derived(error ?? $page.error);
</script>

<div class="error-page">
  {#if displayStatus}
    <h1>{displayStatus}</h1>
  {/if}

  <p>{displayError?.message || "Page Not Found"}</p>

  <a href="/">Go back to the homepage</a>
</div>

<style>
  /* Your styles remain the same */
  .error-page {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 80vh;
    text-align: center;
    font-family: system-ui, sans-serif;
  }
  h1 {
    font-size: 6rem;
    font-weight: 900;
    color: #ff3e00;
    margin: 0;
  }
  p {
    font-size: 1.25rem;
    margin: 0.5rem 0 2rem;
  }
  a {
    color: #ff3e00;
    text-decoration: none;
    font-weight: bold;
  }
  a:hover {
    text-decoration: underline;
  }
</style>
