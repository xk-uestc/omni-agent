# React browser runtime

React and ReactDOM 18.3.1 production UMD distributions, downloaded from the official npm packages `react@18.3.1` and `react-dom@18.3.1`. Both use the MIT license, included in REACT-LICENSE.

The tool timeline is a React component mounted from `tool-timeline.js`. These files are served locally; no runtime CDN connection or JSX transpiler is required. The existing schema and result visualization components retain their original interfaces.

Reproduce the distributions with `npm install --prefix runtime/ui-react-deps react@18.3.1 react-dom@18.3.1 --ignore-scripts --no-audit --no-fund`, then copy the two `umd/*.production.min.js` files. The temporary npm workspace is excluded from Git.
