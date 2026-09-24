import type { ReactNode } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

export const colors = {
  bg: '#f5f6f8',
  card: '#ffffff',
  text: '#1c1f24',
  muted: '#667085',
  accent: '#2f5bea',
  danger: '#c0362c',
  border: '#e4e7ec',
};

export function Screen({ children }: { children: ReactNode }) {
  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {children}
    </ScrollView>
  );
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <View style={styles.card}>
      {title ? <Text style={styles.cardTitle}>{title}</Text> : null}
      {children}
    </View>
  );
}

export function Button(props: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  busy?: boolean;
  variant?: 'primary' | 'secondary';
}) {
  const secondary = props.variant === 'secondary';
  return (
    <Pressable
      accessibilityRole="button"
      onPress={props.onPress}
      disabled={props.disabled || props.busy}
      style={[
        styles.button,
        secondary && styles.buttonSecondary,
        (props.disabled || props.busy) && styles.buttonDisabled,
      ]}
    >
      {props.busy ? (
        <ActivityIndicator color={secondary ? colors.accent : '#fff'} />
      ) : (
        <Text style={[styles.buttonText, secondary && styles.buttonTextSecondary]}>
          {props.label}
        </Text>
      )}
    </Pressable>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <View style={styles.error}>
      <Text style={styles.errorText}>{message}</Text>
    </View>
  );
}

export function BulletList({ items, empty = 'None' }: { items: string[]; empty?: string }) {
  if (items.length === 0) return <Text style={styles.muted}>{empty}</Text>;
  return (
    <View>
      {items.map((item, index) => (
        <Text key={index} style={styles.body}>
          • {item}
        </Text>
      ))}
    </View>
  );
}

export const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 16, gap: 12, maxWidth: 720, width: '100%', alignSelf: 'center' },
  card: {
    backgroundColor: colors.card,
    borderRadius: 12,
    padding: 16,
    gap: 8,
    borderWidth: 1,
    borderColor: colors.border,
  },
  cardTitle: { fontSize: 13, fontWeight: '600', color: colors.muted, textTransform: 'uppercase' },
  title: { fontSize: 22, fontWeight: '700', color: colors.text },
  body: { fontSize: 15, lineHeight: 22, color: colors.text },
  muted: { fontSize: 14, color: colors.muted },
  button: {
    backgroundColor: colors.accent,
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: 'center',
  },
  buttonSecondary: { backgroundColor: 'transparent', borderWidth: 1, borderColor: colors.accent },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  buttonTextSecondary: { color: colors.accent },
  error: { backgroundColor: '#fdecea', borderRadius: 10, padding: 12 },
  errorText: { color: colors.danger, fontSize: 14 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: {
    backgroundColor: '#eef2ff',
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
    color: colors.accent,
    fontSize: 13,
  },
});
