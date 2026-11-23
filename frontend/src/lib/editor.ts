// SPDX-License-Identifier: AGPL-3.0-or-later
import { yaml } from '@codemirror/lang-yaml';
import { EditorState, type Extension } from '@codemirror/state';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorView } from '@codemirror/view';
import { basicSetup } from 'codemirror';

export function extensionsFor(path: string, readOnly = false): Extension[] {
	const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
	return [
		basicSetup,
		/\.ya?ml$/.test(path) ? yaml() : [],
		dark ? oneDark : [],
		EditorView.theme({
			'&': { fontSize: '13px', height: '100%' },
			'.cm-scroller': { fontFamily: 'ui-monospace, monospace' }
		}),
		readOnly ? [EditorState.readOnly.of(true), EditorView.editable.of(false)] : []
	];
}
