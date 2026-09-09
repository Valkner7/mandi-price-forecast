import { NavLink } from 'react-router-dom';

function navItemClass({ isActive }) {
  return 'nav-item' + (isActive ? ' active' : '');
}

export default function Sidebar({ footer }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">Mandi Setu</span>
        <span className="brand-sub">Price forecast desk</span>
      </div>
      <nav>
        <ul className="nav-list">
          <li>
            <NavLink to="/" end className={navItemClass}>Dashboard</NavLink>
          </li>
          <li className="nav-item" onClick={() => { window.location.href = '/voice-test'; }}>
            Voice advisory
          </li>
          <li className="nav-item" onClick={() => { window.location.href = '/trends-dashboard'; }}>
            <span className="nav-label">
              Mandi Rujhan
              <span className="nav-sub">Trend board</span>
            </span>
          </li>
          <li>
            <NavLink to="/nearby" className={navItemClass}>
              <span className="nav-label">
                Mandi Sameep
                <span className="nav-sub">Nearby mandis</span>
              </span>
            </NavLink>
          </li>
          <li className="nav-item" onClick={() => { window.location.href = '/docs'; }}>
            API docs
          </li>
        </ul>
      </nav>
      <div className="sidebar-footer">{footer}</div>
    </aside>
  );
}
