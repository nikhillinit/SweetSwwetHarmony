"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/", label: "Dashboard", icon: "◆" },
  { href: "/companies", label: "Companies", icon: "◇" },
  { href: "/signals", label: "Signals", icon: "○" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 border-r border-neutral-800 bg-neutral-900 flex flex-col">
      <div className="p-6 border-b border-neutral-800">
        <h1 className="text-xl font-semibold tracking-tight">Discovery</h1>
        <p className="text-sm text-neutral-500 mt-1">Press On Ventures</p>
      </div>
      
      <nav className="flex-1 p-4">
        <ul className="space-y-1">
          {navItems.map((item) => {
            const isActive = pathname === item.href;
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                    isActive
                      ? "bg-neutral-800 text-white"
                      : "text-neutral-400 hover:text-white hover:bg-neutral-800/50"
                  }`}
                >
                  <span className="text-lg">{item.icon}</span>
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="p-4 border-t border-neutral-800">
        <div className="flex items-center gap-3 px-3 py-2">
          <div className="w-8 h-8 rounded-full bg-neutral-700 flex items-center justify-center text-sm">
            U
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium truncate">User</p>
            <p className="text-xs text-neutral-500 truncate">Sign in to continue</p>
          </div>
        </div>
      </div>
    </aside>
  );
}
