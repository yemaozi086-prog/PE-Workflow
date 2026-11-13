import {useState} from 'react';
import type {Run} from './types';

const pageSize = 30;

export default function OptimizationResults({result}: {result: NonNullable<Run['optimization']>}) {
  const [page, setPage] = useState(0);
  const history = result.history ?? [];
  const start = page * pageSize;
  const rows = history.slice(start, start + pageSize);
  const paths = Object.keys(result.best_parameters ?? {});

  return <div className="optimization-summary">
    <b>DE · {result.evaluations} 次评价{result.iterations == null ? '' : ` · ${result.iterations} 代`}</b>
    <span>{result.converged ? '达到收敛判据' : (result.message || '未达到收敛判据')}</span>
    <code>{JSON.stringify(result.best_parameters, null, 2)}</code>
    {rows.length > 0 && <details>
      <summary>逐次候选评价</summary>
      <div style={{overflowX: 'auto'}}>
        <table className="result-table">
          <thead><tr><th>评价序号</th>{paths.map(path => <th key={path}>{path}</th>)}<th>目标值</th></tr></thead>
          <tbody>{rows.map(row => <tr key={row.evaluation}>
            <td>{row.evaluation}</td>
            {paths.map(path => <td key={path}>{row.parameters?.[path]?.toPrecision(10) ?? '—'}</td>)}
            <td>{row.objective?.toPrecision(10) ?? '—'}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <div>
        <button className="outline-button" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button>
        <span> {start + 1}–{start + rows.length} / {history.length} </span>
        <button className="outline-button" disabled={start + rows.length >= history.length} onClick={() => setPage(page + 1)}>下一页</button>
      </div>
    </details>}
  </div>;
}
