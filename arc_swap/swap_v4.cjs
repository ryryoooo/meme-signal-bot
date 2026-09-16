/**
 * Arc Uniswap V4 swap via Universal Router (no GMGN).
 * Supports:
 *   - ERC20 USDC pools (0x3600…, 6 decimals) via Permit2 + SETTLE(payerIsUser=true)
 *   - Native-quoted pools (currency address(0), 18-dec Arc gas/USDC) via msg.value + SETTLE(payerIsUser=false)
 * Env:
 *   PRIVATE_KEY | GMGN_PRIVATE_KEY
 *   TOKEN          token address
 *   SIDE           buy|sell  (default buy)
 *   AMOUNT_USD     exact-in USDC for buy (human USD)
 *   AMOUNT_TOKEN   exact-in token raw (or human if AMOUNT_TOKEN_HUMAN=1)
 *   PERCENT        sell percent of balance (1-100); used if AMOUNT_TOKEN unset
 *   SLIPPAGE_BPS   default 5000 (50%)
 *   POOL_FEE / TICK_SPACING / HOOKS
 *   RPC | ARC_RPC
 *   DISCOVER_POOL  1 = probe StateView + DexScreener if needed
 *   DRY_RUN        1 = encode only
 */
const { ethers } = require('ethers');
const { V4Planner, Actions, URVersion } = require('@uniswap/v4-sdk');
const { RoutePlanner, CommandType } = require('@uniswap/universal-router-sdk');

const CHAIN_ID = 5042;
const RPC = process.env.RPC || process.env.ARC_RPC || 'https://rpc.mainnet.arc.io';
const UR = ethers.getAddress('0x4fca4a51ab4f23a7447b3284fbd7d73289a89fb1');
const PERMIT2 = ethers.getAddress('0x000000000022d473030f116ddee9f6b43ac78ba3');
const USDC = ethers.getAddress('0x3600000000000000000000000000000000000000');
const NATIVE = ethers.ZeroAddress;
const QUOTER = ethers.getAddress('0x8dc178efb8111bb0973dd9d722ebeff267c98f94');
const STATE_VIEW = ethers.getAddress('0xf3334192d15450cdd385c8b70e03f9a6bd9e673b');
const POOL_MANAGER = ethers.getAddress('0x8366a39cc670b4001a1121b8f6a443a643e40951');
const ARC_HOOK_CANDIDATES = [
  ethers.ZeroAddress,
  ethers.getAddress('0x20EEad6db6b3d0a4491E9073119DD0EBFF166AcC'),
  ethers.getAddress('0xb6A65950534F061618B4AE102FBcbb8541a8e0cC'),
];
const UR_VER = URVersion.V2_1_1; // Arc UR requires 2.1.1 (minHopPriceX36)

const ERC20_ABI = [
  'function approve(address spender, uint256 amount) returns (bool)',
  'function allowance(address owner, address spender) view returns (uint256)',
  'function balanceOf(address) view returns (uint256)',
  'function decimals() view returns (uint8)',
];
const PERMIT2_ABI = [
  'function approve(address token, address spender, uint160 amount, uint48 expiration)',
  'function allowance(address user, address token, address spender) view returns (uint160 amount, uint48 expiration, uint48 nonce)',
];
const UR_ABI = [
  'function execute(bytes commands, bytes[] inputs, uint256 deadline) payable',
];
const STATE_ABI = [
  'function getLiquidity(bytes32 poolId) view returns (uint128 liquidity)',
];

const FEE_CANDIDATES = [100, 500, 3000, 10000, 20000, 40000, 50000];
const TICK_CANDIDATES = [1, 10, 25, 60, 100, 200];

function jlog(obj) {
  console.log(JSON.stringify(obj));
}

function isNativeAddr(a) {
  return !a || ethers.getAddress(a) === NATIVE;
}

function isQuoteCurrency(a) {
  const x = ethers.getAddress(a);
  return x === USDC || x === NATIVE;
}

function poolIdOf(currency0, currency1, fee, tickSpacing, hooks) {
  return ethers.keccak256(
    ethers.AbiCoder.defaultAbiCoder().encode(
      ['address', 'address', 'uint24', 'int24', 'address'],
      [currency0, currency1, fee, tickSpacing, hooks]
    )
  );
}

function sortPair(a, b) {
  const A = ethers.getAddress(a);
  const B = ethers.getAddress(b);
  if (BigInt(A) < BigInt(B)) return { currency0: A, currency1: B };
  return { currency0: B, currency1: A };
}

async function quoteExactIn(provider, currency0, currency1, fee, tickSpacing, hooks, amountIn, zeroForOne) {
  const iface = new ethers.Interface([
    'function quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes)) view returns (int256,uint256)',
  ]);
  const data = iface.encodeFunctionData('quoteExactInputSingle', [[
    [currency0, currency1, fee, tickSpacing, hooks],
    zeroForOne,
    amountIn,
    '0x',
  ]]);
  const out = await provider.call({ to: QUOTER, data });
  const [amountOut] = iface.decodeFunctionResult('quoteExactInputSingle', out);
  return BigInt(amountOut.toString());
}

async function probeLiquidity(provider, currency0, currency1, fee, tickSpacing, hooks) {
  const state = new ethers.Contract(STATE_VIEW, STATE_ABI, provider);
  const id = poolIdOf(currency0, currency1, fee, tickSpacing, hooks);
  try {
    const liq = await state.getLiquidity(id);
    return BigInt(liq.toString());
  } catch {
    return 0n;
  }
}

async function dexscreenerHint(token) {
  try {
    const url = `https://api.dexscreener.com/latest/dex/tokens/${token}`;
    const res = await fetch(url, { headers: { 'User-Agent': 'arc-swap-v4/1.0' } });
    if (!res.ok) return null;
    const data = await res.json();
    const pairs = (data && data.pairs) || [];
    const usdcL = USDC.toLowerCase();
    const scored = pairs
      .filter((p) => {
        const cid = String(p.chainId || '').toLowerCase();
        return cid.includes('arc') || cid === '5042';
      })
      .map((p) => {
        const base = (p.baseToken && p.baseToken.address || '').toLowerCase();
        const quote = (p.quoteToken && p.quoteToken.address || '').toLowerCase();
        const liq = Number(p.liquidity && p.liquidity.usd) || 0;
        const isNativeQuote =
          base === NATIVE.toLowerCase() || quote === NATIVE.toLowerCase() ||
          base === '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee' ||
          quote === '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee';
        const hasUsdc = base === usdcL || quote === usdcL || isNativeQuote;
        const labels = (p.labels || []).map((x) => String(x).toLowerCase());
        const isV4 = labels.includes('v4') || String(p.dexId || '').includes('v4');
        const isV3 = labels.includes('v3');
        return {
          pair: p, liq, hasUsdc, isNativeQuote,
          feeHint: p.fee != null ? Number(p.fee) : null,
          isV4, isV3, labels,
        };
      })
      .sort((a, b) => {
        if (a.isV4 !== b.isV4) return a.isV4 ? -1 : 1;
        if (a.hasUsdc !== b.hasUsdc) return a.hasUsdc ? -1 : 1;
        // Prefer ERC20 USDC over native when both quote
        if (a.isNativeQuote !== b.isNativeQuote) return a.isNativeQuote ? 1 : -1;
        return b.liq - a.liq;
      });
    if (!scored.length) return null;
    const best = scored[0];
    const anyV4 = scored.some((s) => s.isV4);
    jlog({
      step: 'dexscreener', liqUsd: best.liq, hasUsdc: best.hasUsdc,
      isNativeQuote: best.isNativeQuote, feeHint: best.feeHint,
      dex: best.pair.dexId, labels: best.labels, anyV4,
    });
    return { ...best, anyV4, all: scored };
  } catch (e) {
    jlog({ step: 'dexscreener_fail', error: e.message });
    return null;
  }
}

async function resolvePoolKeyFromDexId(provider, poolId) {
  const topic0 = ethers.id('Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)');
  const iface = new ethers.Interface([
    'event Initialize(bytes32 indexed id, address indexed currency0, address indexed currency1, uint24 fee, int24 tickSpacing, address hooks, uint160 sqrtPriceX96, int24 tick)',
  ]);
  const latest = await provider.getBlockNumber();
  const idTopic = ethers.zeroPadValue(poolId, 32);
  for (let end = latest; end > Math.max(0, latest - 250000); end -= 8000) {
    const start = Math.max(0, end - 7999);
    let logs = [];
    try {
      logs = await provider.getLogs({
        address: POOL_MANAGER,
        topics: [topic0, idTopic],
        fromBlock: start,
        toBlock: end,
      });
    } catch {
      continue;
    }
    if (!logs.length) continue;
    const parsed = iface.parseLog(logs[0]);
    const fee = Number(parsed.args.fee);
    const tickSpacing = Number(parsed.args.tickSpacing);
    const hooks = ethers.getAddress(parsed.args.hooks);
    const currency0 = ethers.getAddress(parsed.args.currency0);
    const currency1 = ethers.getAddress(parsed.args.currency1);
    const liq = await probeLiquidity(provider, currency0, currency1, fee, tickSpacing, hooks);
    jlog({
      step: 'pool_from_dex_id', poolId, fee, tickSpacing, hooks, currency0, currency1,
      liquidity: liq.toString(),
    });
    return { fee, tickSpacing, hooks, currency0, currency1, liquidity: liq, poolId };
  }
  return null;
}

async function discoverPool(provider, token, hooks, preferFee, preferTick) {
  const hookList = [];
  const h0 = hooks || ethers.ZeroAddress;
  for (const h of [h0, ...ARC_HOOK_CANDIDATES]) {
    const hh = ethers.getAddress(h);
    if (!hookList.some((x) => x.toLowerCase() === hh.toLowerCase())) hookList.push(hh);
  }
  const ordered = [];
  // Arc hooked memes often use fee=0 + tickSpacing=200 or 25
  ordered.push([0, 200], [0, 25], [0, 100], [0, 60]);
  if (preferFee != null && preferTick != null) ordered.push([preferFee, preferTick]);
  ordered.push([40000, 200], [10000, 100], [3000, 60], [50000, 200]);
  for (const f of FEE_CANDIDATES) {
    for (const ts of TICK_CANDIDATES) {
      if (!ordered.some(([ff, tt]) => ff === f && tt === ts)) ordered.push([f, ts]);
    }
  }
  // Prefer ERC20 USDC pairs, then native-quoted
  const quoteCandidates = [USDC, NATIVE];
  let best = null;
  for (const quote of quoteCandidates) {
    const { currency0, currency1 } = sortPair(quote, token);
    for (const h of hookList) {
      for (const [fee, tickSpacing] of ordered) {
        const liq = await probeLiquidity(provider, currency0, currency1, fee, tickSpacing, h);
        if (liq > 0n) {
          jlog({
            step: 'pool_probe', fee, tickSpacing, hooks: h,
            currency0, currency1, liquidity: liq.toString(),
            quoteKind: isNativeAddr(quote) ? 'native' : 'erc20_usdc',
          });
          const cand = {
            fee, tickSpacing, hooks: h, liquidity: liq,
            currency0, currency1,
            isNative: isNativeAddr(currency0) || isNativeAddr(currency1),
          };
          // Prefer higher liquidity; among equals prefer ERC20 USDC
          if (!best || liq > best.liquidity || (liq === best.liquidity && best.isNative && !cand.isNative)) {
            best = cand;
          }
        }
      }
    }
    // If we already found an ERC20 USDC pool, stop (preferred)
    if (best && !best.isNative) break;
  }
  return best;
}

async function ensurePermit2(wallet, tokenAddr, amountIn) {
  if (isNativeAddr(tokenAddr)) return;
  const token = new ethers.Contract(tokenAddr, ERC20_ABI, wallet);
  const permit2 = new ethers.Contract(PERMIT2, PERMIT2_ABI, wallet);
  const allow = await token.allowance(wallet.address, PERMIT2);
  if (BigInt(allow) < amountIn) {
    const txa = await token.approve(PERMIT2, ethers.MaxUint256);
    jlog({ step: 'approve_permit2', token: tokenAddr, hash: txa.hash });
    await txa.wait();
  }
  const [p2amt, p2exp] = await permit2.allowance(wallet.address, tokenAddr, UR);
  const now = BigInt(Math.floor(Date.now() / 1000));
  if (BigInt(p2amt) < amountIn || BigInt(p2exp) <= now + 60n) {
    const exp = now + 30n * 24n * 3600n;
    const txp = await permit2.approve(tokenAddr, UR, (1n << 160n) - 1n, exp);
    jlog({ step: 'permit2_approve_ur', token: tokenAddr, hash: txp.hash });
    await txp.wait();
  }
}

function encodeV4Swap({ currency0, currency1, fee, tickSpacing, hooks, zeroForOne, amountIn, minOut, tokenIn, tokenOut, settlePayerIsUser }) {
  const planner = new V4Planner();
  const poolKey = { currency0, currency1, fee, tickSpacing, hooks };
  planner.addAction(
    Actions.SWAP_EXACT_IN_SINGLE,
    [{
      poolKey,
      zeroForOne,
      amountIn: amountIn.toString(),
      amountOutMinimum: minOut.toString(),
      minHopPriceX36: 0,
      hookData: '0x',
    }],
    UR_VER
  );
  planner.addAction(Actions.SETTLE, [tokenIn, amountIn.toString(), settlePayerIsUser]);
  planner.addAction(Actions.TAKE_ALL, [tokenOut, minOut.toString()]);
  const v4Encoded = planner.finalize();
  const routePlanner = new RoutePlanner();
  routePlanner.addCommand(CommandType.V4_SWAP, [v4Encoded]);
  return routePlanner;
}

async function main() {
  const pk = process.env.GMGN_PRIVATE_KEY || process.env.PRIVATE_KEY;
  if (!pk) throw new Error('missing private key');
  const tokenRaw = (process.env.TOKEN || process.env.TOKEN_OUT || '').trim();
  if (!/^0x[a-fA-F0-9]{40}$/.test(tokenRaw)) throw new Error('TOKEN required');
  const token = ethers.getAddress(tokenRaw);
  const side = (process.env.SIDE || 'buy').toLowerCase();
  if (side !== 'buy' && side !== 'sell') throw new Error('SIDE must be buy|sell');
  const slippageBps = BigInt(process.env.SLIPPAGE_BPS || '5000');
  let fee = process.env.POOL_FEE ? Number(process.env.POOL_FEE) : null;
  let tickSpacing = process.env.TICK_SPACING ? Number(process.env.TICK_SPACING) : null;
  let hooks = process.env.HOOKS ? ethers.getAddress(process.env.HOOKS) : ethers.ZeroAddress;
  const dry = process.env.DRY_RUN === '1';
  const discover = process.env.DISCOVER_POOL !== '0';

  const provider = new ethers.JsonRpcProvider(RPC, CHAIN_ID);
  const wallet = new ethers.Wallet(pk.startsWith('0x') ? pk : '0x' + pk, provider);
  const usdc = new ethers.Contract(USDC, ERC20_ABI, wallet);
  const tok = new ethers.Contract(token, ERC20_ABI, wallet);
  const router = new ethers.Contract(UR, UR_ABI, wallet);

  // Currencies filled after pool discovery (may be native 0x0 or ERC20 USDC)
  let currency0 = null;
  let currency1 = null;
  let poolIsNative = false;

  if (process.env.POOL_FEE && process.env.TICK_SPACING) {
    fee = Number(process.env.POOL_FEE);
    tickSpacing = Number(process.env.TICK_SPACING);
    // Optional override: POOL_QUOTE=native|usdc (default probe both via currencies if set)
    const quoteOverride = (process.env.POOL_QUOTE || '').toLowerCase();
    const quote = quoteOverride === 'native' ? NATIVE : USDC;
    ({ currency0, currency1 } = sortPair(quote, token));
    poolIsNative = isNativeAddr(currency0) || isNativeAddr(currency1);
    jlog({ step: 'pool_env', fee, tickSpacing, hooks, currency0, currency1, poolIsNative });
  } else {
    fee = fee || 40000;
    tickSpacing = tickSpacing || 200;
    if (discover) {
      const dex = await dexscreenerHint(token);
      let found = null;
      if (dex && dex.anyV4 && dex.all) {
        const v4pairs = [...dex.all].filter((s) => s.isV4).sort((a, b) => {
          // Prefer ERC20 USDC, then native quote, then liquidity
          const score = (s) => {
            if (!s.hasUsdc) return 0;
            if (s.isNativeQuote) return 1;
            return 2;
          };
          const d = score(b) - score(a);
          if (d) return d;
          return b.liq - a.liq;
        });
        for (const s of v4pairs.slice(0, 6)) {
          const pid = s.pair && s.pair.pairAddress;
          if (!pid || !String(pid).startsWith('0x') || String(pid).length !== 66) continue;
          found = await resolvePoolKeyFromDexId(provider, pid);
          if (found && found.liquidity > 0n) {
            const involvesQuote =
              isQuoteCurrency(found.currency0) || isQuoteCurrency(found.currency1);
            if (!involvesQuote) { found = null; continue; }
            break;
          }
          found = null;
        }
      }
      if (!found) {
        found = await discoverPool(provider, token, hooks, fee, tickSpacing);
      }
      if (found) {
        fee = found.fee;
        tickSpacing = found.tickSpacing;
        if (found.hooks) hooks = ethers.getAddress(found.hooks);
        currency0 = ethers.getAddress(found.currency0);
        currency1 = ethers.getAddress(found.currency1);
        poolIsNative = isNativeAddr(currency0) || isNativeAddr(currency1);
        jlog({
          step: 'pool_chosen', fee, tickSpacing, hooks,
          currency0, currency1, poolIsNative,
          liquidity: found.liquidity.toString(),
        });
      } else {
        const onlyV3 = dex && dex.anyV4 === false && (dex.all || []).some((s) => s.isV3);
        jlog({
          step: 'pool_missing', fee, tickSpacing,
          note: onlyV3 ? 'dexscreener_v3_only_no_v4_pool' : 'no_v4_liquidity',
        });
        throw new Error(onlyV3 ? 'no_v4_pool (token appears Uniswap v3-only on Arc)' : 'no_v4_pool');
      }
    } else {
      ({ currency0, currency1 } = sortPair(USDC, token));
      poolIsNative = false;
    }
  }

  if (!currency0 || !currency1) {
    ({ currency0, currency1 } = sortPair(poolIsNative ? NATIVE : USDC, token));
  }

  const tokenIsC0 = currency0.toLowerCase() === token.toLowerCase();
  const zeroForOne = side === 'buy' ? !tokenIsC0 : tokenIsC0;

  // Quote currency for this pool (native or ERC20 USDC)
  const quoteCurrency = isNativeAddr(currency0)
    ? NATIVE
    : (isNativeAddr(currency1) ? NATIVE
      : (currency0.toLowerCase() === USDC.toLowerCase() ? USDC
        : (currency1.toLowerCase() === USDC.toLowerCase() ? USDC : currency0)));
  const quoteIsNative = isNativeAddr(quoteCurrency);

  let amountIn;
  if (side === 'buy') {
    const amountUsd = process.env.AMOUNT_USD || '1';
    if (quoteIsNative) {
      // Arc native gas = USDC at 18 decimals
      amountIn = BigInt(Math.round(parseFloat(amountUsd) * 1e18));
      const bal = await provider.getBalance(wallet.address);
      jlog({
        step: 'bal', side, wallet: wallet.address,
        native: bal.toString(), amountIn: amountIn.toString(),
        quoteKind: 'native_18dec',
      });
      if (bal < amountIn) throw new Error('insufficient native USDC');
    } else {
      amountIn = BigInt(Math.round(parseFloat(amountUsd) * 1e6));
      const bal = await usdc.balanceOf(wallet.address);
      jlog({
        step: 'bal', side, wallet: wallet.address,
        usdc: bal.toString(), amountIn: amountIn.toString(),
        quoteKind: 'erc20_usdc_6dec',
      });
      if (bal < amountIn) throw new Error('insufficient USDC');
    }
  } else {
    const tokBal = await tok.balanceOf(wallet.address);
    const dec = Number(await tok.decimals());
    if (process.env.AMOUNT_TOKEN) {
      if (process.env.AMOUNT_TOKEN_HUMAN === '1') {
        amountIn = BigInt(Math.round(parseFloat(process.env.AMOUNT_TOKEN) * 10 ** dec));
      } else {
        amountIn = BigInt(process.env.AMOUNT_TOKEN);
      }
    } else {
      const pct = Math.max(1, Math.min(100, parseInt(process.env.PERCENT || '100', 10)));
      amountIn = (tokBal * BigInt(pct)) / 100n;
    }
    jlog({
      step: 'bal', side, wallet: wallet.address,
      tokenBal: tokBal.toString(), amountIn: amountIn.toString(), decimals: dec,
      quoteKind: quoteIsNative ? 'native_18dec' : 'erc20_usdc_6dec',
    });
    if (amountIn <= 0n) throw new Error('zero sell amount');
    if (tokBal < amountIn) throw new Error('insufficient token balance');
  }

  const amountOut = await quoteExactIn(
    provider, currency0, currency1, fee, tickSpacing, hooks, amountIn, zeroForOne
  );
  const minOut = amountOut - (amountOut * slippageBps) / 10000n;
  jlog({
    step: 'quote',
    amountOut: amountOut.toString(),
    minOut: minOut.toString(),
    fee, tickSpacing, zeroForOne, currency0, currency1,
    poolIsNative, quoteIsNative, urVersion: UR_VER,
  });

  const tokenIn = side === 'buy' ? quoteCurrency : token;
  const tokenOut = side === 'buy' ? token : quoteCurrency;

  // Native settle: UR SDK funds via msg.value and SETTLE(..., payerIsUser=false)
  // ERC20 USDC: Permit2 + SETTLE(..., payerIsUser=true)
  const settlePayerIsUser = !isNativeAddr(tokenIn);
  const msgValue = (side === 'buy' && isNativeAddr(tokenIn)) ? amountIn : 0n;

  const { commands, inputs } = encodeV4Swap({
    currency0, currency1, fee, tickSpacing, hooks,
    zeroForOne, amountIn, minOut, tokenIn, tokenOut, settlePayerIsUser,
  });
  jlog({
    step: 'encoded', commands, inputs0Len: inputs[0].length,
    settlePayerIsUser, msgValue: msgValue.toString(),
    tokenIn, tokenOut,
  });

  if (dry) {
    jlog({ step: 'dry_ok', side, amountIn: amountIn.toString(), amountOut: amountOut.toString() });
    return;
  }

  await ensurePermit2(wallet, tokenIn, amountIn);

  const deadline = Math.floor(Date.now() / 1000) + 600;

  // Primary path
  let usedSettlePayerIsUser = settlePayerIsUser;
  let usedCommands = commands;
  let usedInputs = inputs;
  try {
    const data = router.interface.encodeFunctionData('execute', [commands, inputs, deadline]);
    const opts = { from: wallet.address, to: UR, data, gasLimit: 2_000_000n };
    if (msgValue > 0n) opts.value = msgValue;
    await provider.call(opts);
    jlog({ step: 'static_ok', settlePayerIsUser, msgValue: msgValue.toString() });
  } catch (e) {
    jlog({ step: 'static_fail', settlePayerIsUser, error: e.shortMessage || e.message });
    // Fallback: if native buy with payerIsUser=false failed, try payerIsUser=true (still with msg.value)
    if (msgValue > 0n && settlePayerIsUser === false) {
      const alt = encodeV4Swap({
        currency0, currency1, fee, tickSpacing, hooks,
        zeroForOne, amountIn, minOut, tokenIn, tokenOut, settlePayerIsUser: true,
      });
      try {
        const data2 = router.interface.encodeFunctionData('execute', [alt.commands, alt.inputs, deadline]);
        const opts2 = { from: wallet.address, to: UR, data: data2, gasLimit: 2_000_000n, value: msgValue };
        await provider.call(opts2);
        jlog({ step: 'static_ok_fallback', settlePayerIsUser: true, msgValue: msgValue.toString() });
        usedSettlePayerIsUser = true;
        usedCommands = alt.commands;
        usedInputs = alt.inputs;
      } catch (e2) {
        jlog({ step: 'static_fail_fallback', settlePayerIsUser: true, error: e2.shortMessage || e2.message });
        throw e;
      }
    } else {
      throw e;
    }
  }

  const balInBefore = side === 'buy'
    ? (quoteIsNative ? await provider.getBalance(wallet.address) : await usdc.balanceOf(wallet.address))
    : await tok.balanceOf(wallet.address);
  const balOutBefore = side === 'buy'
    ? await tok.balanceOf(wallet.address)
    : (quoteIsNative ? await provider.getBalance(wallet.address) : await usdc.balanceOf(wallet.address));

  const txOpts = { gasLimit: 2_000_000n };
  if (msgValue > 0n) txOpts.value = msgValue;
  const tx = await router.execute(usedCommands, usedInputs, deadline, txOpts);
  jlog({ step: 'swap_sent', hash: tx.hash, settlePayerIsUser: usedSettlePayerIsUser });
  const receipt = await tx.wait();

  const balInAfter = side === 'buy'
    ? (quoteIsNative ? await provider.getBalance(wallet.address) : await usdc.balanceOf(wallet.address))
    : await tok.balanceOf(wallet.address);
  const balOutAfter = side === 'buy'
    ? await tok.balanceOf(wallet.address)
    : (quoteIsNative ? await provider.getBalance(wallet.address) : await usdc.balanceOf(wallet.address));

  // Native in/out deltas include gas; for buy spent use amountInSpent approx via receipt gas
  let amountInSpent = balInBefore - balInAfter;
  let amountOutReceived = balOutAfter - balOutBefore;
  if (side === 'buy' && quoteIsNative) {
    const gasCost = receipt.gasUsed * (receipt.gasPrice || 0n);
    // balIn delta includes gas; report swap portion as amountIn if close
    amountInSpent = amountIn; // exact-in
    void gasCost;
  }
  if (side === 'sell' && quoteIsNative) {
    // balOut delta is reduced by gas paid in same tx — still informative
  }

  let tokenDecimals = 18;
  try {
    tokenDecimals = Number(await tok.decimals());
  } catch (_) {
    tokenDecimals = 18;
  }
  const quoteKind = quoteIsNative ? 'native_18dec' : 'erc20_usdc_6dec';
  let fillPriceUsd = null;
  if (side === 'buy' && amountOutReceived > 0n) {
    const usdIn = quoteIsNative
      ? Number(amountInSpent) / 1e18
      : Number(amountInSpent) / 1e6;
    const tokensOut = Number(amountOutReceived) / (10 ** tokenDecimals);
    if (tokensOut > 0 && usdIn > 0) fillPriceUsd = usdIn / tokensOut;
  } else if (side === 'sell' && amountInSpent > 0n && amountOutReceived > 0n) {
    const usdOut = quoteIsNative
      ? Number(amountOutReceived) / 1e18
      : Number(amountOutReceived) / 1e6;
    const tokensIn = Number(amountInSpent) / (10 ** tokenDecimals);
    if (tokensIn > 0 && usdOut > 0) fillPriceUsd = usdOut / tokensIn;
  }

  const result = {
    step: 'done',
    ok: receipt.status === 1,
    status: receipt.status,
    hash: tx.hash,
    tx_hash: tx.hash,
    side,
    amountIn: amountIn.toString(),
    amountOutQuoted: amountOut.toString(),
    amountInSpent: amountInSpent.toString(),
    amountOutReceived: amountOutReceived.toString(),
    gasUsed: receipt.gasUsed.toString(),
    block: receipt.blockNumber,
    fee,
    tickSpacing,
    poolIsNative,
    quoteIsNative,
    quoteKind,
    decimals: tokenDecimals,
    tokenDecimals,
    fillPriceUsd,
    settlePayerIsUser: usedSettlePayerIsUser,
    currency0,
    currency1,
  };
  jlog(result);
  if (receipt.status !== 1) process.exit(2);
}

main().catch((e) => {
  console.error(JSON.stringify({ error: e.shortMessage || e.reason || e.message || String(e), ok: false }));
  process.exit(1);
});
