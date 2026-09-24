import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

export default function RootLayout() {
  return (
    <>
      <StatusBar style="dark" />
      <Stack>
        <Stack.Screen name="index" options={{ title: 'Voice Analytics AI' }} />
        <Stack.Screen name="analysis/[id]/index" options={{ title: 'Processing' }} />
        <Stack.Screen name="analysis/[id]/result" options={{ title: 'Result' }} />
      </Stack>
    </>
  );
}
