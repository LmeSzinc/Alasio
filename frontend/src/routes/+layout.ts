// Disable server side rendering.
// Because we use python backend, no javascript server side
export const ssr = false;
// Render every route as html.
// Even though we have dynamic JWT authorization, we still create static html,
// so user can directly access specific route instead of going to main page everytime.
// The downside is html/css/js will transfer to unauthorized access before they get redirected.
// It's acceptable compares to the benifit. Alasio is just a local service with remote assess,
// not a high level security thingy. Backend will reject unauthorized access anyway,
// so no actual data will be transfered.
export const prerender = true;
