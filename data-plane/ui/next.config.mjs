/** Static export: Databricks Apps runs a single command (uvicorn), so there is
 *  no Node server to host Next. Exporting to plain files lets the existing
 *  FastAPI app serve the UI with no backend changes beyond where it looks. */
const nextConfig = {
  output: "export",
  distDir: "out",
  images: { unoptimized: true },
  trailingSlash: false,
};
export default nextConfig;
