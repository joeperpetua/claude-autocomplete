export interface Person {
  fullName: string;
  age: number;
}

export function getNames(people: Person[]): string[] {
  return people.map((person) => person.fullName);
}

export const people: Person[] = [{ fullName: "Ada Lovelace", age: 36 }];
