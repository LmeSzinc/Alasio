<script lang="ts">
	import { Button } from '$lib/components/ui/button/index.js';
	import { Label } from '$lib/components/ui/label/index.js';
	import { Input } from '$lib/components/ui/input/index.js';
	import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '$lib/components/ui/card/index.js';
	import { Help } from '$lib/components/ui/help/index.js';
	import { authApi, type jwtError } from '$lib/api/auth';
  import { goto } from '$app/navigation';

	let showTip = $state(false);
	let error = $state<jwtError | null>(null);
	let password = $state("");

	async function login() {
    // close tip
    showTip = false;
		try {
			let response = await authApi.login.call(password);
			
					// Success cases: 200 or 204 redirect to home
			if (response.is(200) || response.is(204)) {
				goto('/');
				return;
			}
			
			// Error cases: 429, 403, 500 show error messages
			if (response.is(401) || response.is(403) || response.is(429)) {
				error = response.data;
			}
		} catch (err) {
			// Handle network errors or other exceptions
			error = {
        message: "Network error, please check your connection",
        remain: 0,
        after: 600,
      };
		}
	}

	// Handle form submission
	function handleSubmit(event: Event) {
		event.preventDefault();
		login();
	}
</script>

<Card class="w-full max-w-sm mx-auto mt-6 gap-4">
	<CardHeader>
		<CardTitle class="mx-auto text-xl">
			Login to Alasio
		</CardTitle>
	</CardHeader>
	<CardContent>
		<form onsubmit={handleSubmit}>
			<div class="flex flex-col">
				<div class="grid gap-2">
					<div class="flex items-center">
						<Label for="password" class="text-sm">Password</Label>
						<Button
							variant="link"
							class="ml-auto text-xs text-muted-foreground p-0 h-auto"
							onclick={() => showTip = !showTip}
						>
							Forgot password?
						</Button>
					</div>
					<Input 
						id="password" 
						type="password" 
						bind:value={password} 
					/>
					{#if showTip}
						<Help>
							If you forgot your password, you can always peep in file "/config/deploy.yaml" key "Password"
						</Help>
					{/if}
					{#if error}
						<Help variant="error">
							{#if error.message === "failure"}
								Login failed, incorrect password.<br>
                Remaining attempts: {error.remain}
							{:else if error.message === "banned"}
								IP temporarily banned.<br>
                please try again in {Math.ceil(error.after / 60)} minutes
							{:else}
								{error.message}
							{/if}
						</Help>
					{/if}
				</div>
			</div>
		</form>
	</CardContent>
	<CardFooter class="flex-col gap-2">
		<Button
			type="submit"
			class="w-full"
			onclick={login}
		>
			Login
		</Button>
	</CardFooter>
</Card>