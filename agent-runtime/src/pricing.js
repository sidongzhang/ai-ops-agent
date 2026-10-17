// Explicit model-bound versioned prices in USD per million tokens. No alias guessing.
export function loadPrice(env,model) {
  if(!env.PI_PRICE_PROFILE) return null;
  const p=JSON.parse(env.PI_PRICE_PROFILE);
  if(p.model!==model || p.currency!=='USD' || typeof p.version!=='string' || !p.version || p.version.length>64 || typeof p.source!=='string' || !p.source ||
     !['input','output','cacheRead','cacheWrite'].every(k=>Number.isFinite(p[k]) && p[k]>=0)) throw new Error('Invalid model-bound price profile');
  return p;
}
export function estimateCost(usage,p) {
  if(!p) return null;
  const cost=((usage.input_tokens-usage.cache_read_tokens-usage.cache_write_tokens)*p.input+
    usage.cache_read_tokens*p.cacheRead+usage.cache_write_tokens*p.cacheWrite+usage.output_tokens*p.output)/1e6;
  return Number.isFinite(cost)?cost:null;
}
