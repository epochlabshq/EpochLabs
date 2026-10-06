// Stand-in for optional x402 payment packages that the Coinbase wallet SDK imports but this site never calls.
const unavailable = (): never => {
  throw new Error('x402 payments are not available on this site.');
};

export const toClientEvmSigner = unavailable;
export const registerExactEvmScheme = unavailable;
export const x402Client = unavailable;
export const ExactEvmScheme = unavailable;
export const ExactEvmSchemeV1 = unavailable;
export const UptoEvmScheme = unavailable;
export const ExactSvmScheme = unavailable;
export const ExactSvmSchemeV1 = unavailable;
export const UptoSvmScheme = unavailable;
