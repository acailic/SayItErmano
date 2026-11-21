/* Paddle checkout constants (#17) — the ONE file to flip for go-live.

   Sandbox first: paste the sandbox values from the Paddle dashboard and
   the site opens sandbox checkouts (test purchases). When the store goes
   live, set mode to "live" and swap in the live values. Steps:
   site/README.md ("Paddle checkout").
*/
window.SAYIT_PADDLE = {
  mode: "sandbox",      // "sandbox" | "live"
  clientToken: "",      // Paddle.js client-side token ("test_…" / "live_…")
  priceId: "",          // supporter-license price id ("pri_test_…" / "pri_…")
  paymentLink: ""       // hosted checkout link (no-JS / new-tab fallback)
};
